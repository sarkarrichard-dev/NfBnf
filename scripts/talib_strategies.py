"""Two new strategies built on TA-Lib indicators, run through backtrader's
Cerebro engine -- added 2026-09-18 at Richard's request to actually use both
libraries (previously installed but untouched) rather than leave them idle.

Both strategies do their own trade bookkeeping (``self.trades``, a plain list
of dicts) instead of relying on backtrader's built-in trade/commission model.
Real costs are applied afterward by the runner script
(``scripts/backtest_talib_backtrader.py``), using this platform's own
already-trusted charge functions (``index_ai.charges`` / ``crypto.charges``),
so the comparison to every other strategy on this platform stays apples-to-
apples rather than inventing a second cost model.

Genuinely untried on this platform before today:
  * ``TALibCandleStrategy`` -- TA-Lib's built-in candlestick-pattern
    recognisers (far more of them than the hand-rolled
    ``index_ai.strategies.candlestick_patterns``), fired at a real N-bar
    swing support/resistance level, filtered by trend (price vs EMA).
  * ``TALibMacdStochStrategy`` -- MACD histogram cross + Stochastic %K/%D
    reclaim from oversold/overbought, gated by ADX so it only fires in a
    real trend, not chop.

Both exit through the same two-phase shape used everywhere else on this
platform (CLAUDE.md / memory/standard-trailing-stop-and-profit.md): a stop
that protects capital, then -- once the trade has moved far enough in favor
-- a tighter trailing floor that locks in profit. Expressed in ATR multiples
since these are point-based futures/perp instruments, not the P&L-percent
version crypto's own strategies use.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import talib

# A curated set of TA-Lib's candlestick-pattern functions -- not all ~60,
# just the well-known reversal/continuation shapes with a real literature
# behind them. Each returns +100 (bullish) / -100 (bearish) / 0 per bar.
_PATTERN_FNS = [
    talib.CDLENGULFING,
    talib.CDLHAMMER,
    talib.CDLSHOOTINGSTAR,
    talib.CDLMORNINGSTAR,
    talib.CDLEVENINGSTAR,
    talib.CDLHARAMI,
    talib.CDL3LINESTRIKE,
    talib.CDLPIERCING,
    talib.CDLDARKCLOUDCOVER,
    talib.CDL3WHITESOLDIERS,
    talib.CDL3BLACKCROWS,
]


def _pattern_votes(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> tuple[int, int, list[str]]:
    """(bullish_count, bearish_count, fired_names) on the last closed bar."""
    bull = bear = 0
    fired: list[str] = []
    for fn in _PATTERN_FNS:
        try:
            v = fn(o, h, lo, c)[-1]
        except Exception:
            continue
        if v > 0:
            bull += 1
            fired.append(fn.__name__)
        elif v < 0:
            bear += 1
            fired.append(fn.__name__)
    return bull, bear, fired


class _TwoPhaseExitMixin:
    """Shared position/exit bookkeeping every strategy here uses: a plain
    ``self.trades`` list of closed-trade dicts, and an ATR chandelier exit
    with a breakeven-then-trail two-phase shape."""

    def _reset_position_state(self) -> None:
        self.side: str | None = None
        self.entry_price = 0.0
        self.entry_dt = None
        self.entry_reason = ""
        self.stop_price = 0.0
        self.peak = 0.0
        self.armed = False

    def _enter(self, side: str, price: float, atr: float, reason: str) -> None:
        self.side = side
        self.entry_price = price
        self.entry_dt = self.data.datetime.datetime(0)
        self.entry_reason = reason
        self.peak = price
        self.armed = False
        if side == "long":
            self.stop_price = price - self.p.atr_mult_stop * atr
            self.buy(size=1)
        else:
            self.stop_price = price + self.p.atr_mult_stop * atr
            self.sell(size=1)

    def _exit(self, price: float, reason: str) -> None:
        self.trades.append(
            {
                "side": self.side,
                "entry_price": self.entry_price,
                "exit_price": price,
                "entry_dt": self.entry_dt,
                "exit_dt": self.data.datetime.datetime(0),
                "exit_reason": reason,
                "entry_reason": self.entry_reason,
            }
        )
        self.close()
        self._reset_position_state()

    def _manage(self, price_high: float, price_low: float, price_close: float, atr: float) -> None:
        """Check the current stop against this bar's range, then update the
        stop for the next bar. Breakeven-or-better once armed (+arm_r*ATR in
        favor), then trails ``trail_atr_mult`` * ATR behind the peak."""
        direction = 1 if self.side == "long" else -1
        favorable = (price_close - self.entry_price) * direction

        if direction == 1:
            self.peak = max(self.peak, price_high)
        else:
            self.peak = min(self.peak, price_low)

        if not self.armed and favorable >= self.p.arm_r * atr:
            self.armed = True
            # first arm: lift the stop to breakeven, never worse
            self.stop_price = max(self.stop_price, self.entry_price) if direction == 1 else min(self.stop_price, self.entry_price)

        if self.armed:
            trail = self.p.trail_atr_mult * atr
            candidate = (self.peak - trail) if direction == 1 else (self.peak + trail)
            self.stop_price = max(self.stop_price, candidate) if direction == 1 else min(self.stop_price, candidate)

        hit = (direction == 1 and price_low <= self.stop_price) or (direction == -1 and price_high >= self.stop_price)
        if hit:
            reason = "trailing profit" if self.armed else "stop"
            self._exit(self.stop_price, reason)


class TALibCandleStrategy(_TwoPhaseExitMixin, bt.Strategy):
    """TA-Lib candlestick pattern at a real N-bar swing support/resistance
    level, filtered by trend (price vs a slow EMA) -- long on a bullish
    pattern at support in an uptrend, short on a bearish pattern at
    resistance in a downtrend."""

    params = dict(
        sr_lookback=20,
        ema_period=50,
        atr_period=14,
        atr_mult_stop=2.0,
        arm_r=1.5,
        trail_atr_mult=1.0,
        near_pct=0.3,  # within 0.3% of the swing level counts as "at" it
        min_bars=60,
    )

    def __init__(self):
        self.atr = bt.indicators.ATR(period=self.p.atr_period)
        self.ema = bt.indicators.EMA(period=self.p.ema_period)
        self.trades: list[dict] = []
        self._reset_position_state()

    def next(self):
        if len(self.data) < max(self.p.min_bars, self.p.ema_period + 5):
            return
        price_c = float(self.data.close[0])
        price_h = float(self.data.high[0])
        price_l = float(self.data.low[0])
        atr = float(self.atr[0])
        if atr <= 0:
            return

        if self.side is not None:
            self._manage(price_h, price_l, price_c, atr)
            return

        n = self.p.sr_lookback
        window = min(n + 5, len(self.data))
        o = np.array(self.data.open.get(size=window))
        h = np.array(self.data.high.get(size=window))
        lo = np.array(self.data.low.get(size=window))
        c = np.array(self.data.close.get(size=window))
        if len(c) < window:
            return

        bull, bear, fired = _pattern_votes(o, h, lo, c)
        recent_low = float(np.min(lo[:-1][-n:])) if len(lo) > n else float(np.min(lo[:-1]))
        recent_high = float(np.max(h[:-1][-n:])) if len(h) > n else float(np.max(h[:-1]))
        near_support = price_c <= recent_low * (1 + self.p.near_pct / 100.0)
        near_resistance = price_c >= recent_high * (1 - self.p.near_pct / 100.0)
        trend_up = price_c > float(self.ema[0])

        if bull > 0 and near_support and trend_up:
            self._enter("long", price_c, atr, f"{'/'.join(fired)} at support {recent_low:.2f}")
        elif bear > 0 and near_resistance and not trend_up:
            self._enter("short", price_c, atr, f"{'/'.join(fired)} at resistance {recent_high:.2f}")


class TALibMacdStochStrategy(_TwoPhaseExitMixin, bt.Strategy):
    """MACD histogram cross + Stochastic %K/%D reclaim from an extreme,
    gated by ADX so it only fires in a real trend."""

    params = dict(
        fast=12,
        slow=26,
        signal=9,
        stoch_k=14,
        stoch_d=3,
        stoch_smooth=3,
        adx_period=14,
        adx_min=20.0,
        oversold=20.0,
        overbought=80.0,
        atr_period=14,
        atr_mult_stop=2.0,
        arm_r=1.5,
        trail_atr_mult=1.0,
        min_bars=80,
    )

    def __init__(self):
        self.atr = bt.indicators.ATR(period=self.p.atr_period)
        self.trades: list[dict] = []
        self._reset_position_state()
        self._prev_hist = None
        self._prev_k = None
        self._prev_d = None

    def next(self):
        need = max(self.p.min_bars, self.p.slow + self.p.signal + 5)
        if len(self.data) < need:
            return
        price_c = float(self.data.close[0])
        price_h = float(self.data.high[0])
        price_l = float(self.data.low[0])
        atr = float(self.atr[0])
        if atr <= 0:
            return

        if self.side is not None:
            self._manage(price_h, price_l, price_c, atr)
            return

        window = need
        h = np.array(self.data.high.get(size=window))
        lo = np.array(self.data.low.get(size=window))
        c = np.array(self.data.close.get(size=window))
        if len(c) < window:
            return

        macd, macd_sig, macd_hist = talib.MACD(
            c, fastperiod=self.p.fast, slowperiod=self.p.slow, signalperiod=self.p.signal
        )
        k, d = talib.STOCH(
            h,
            lo,
            c,
            fastk_period=self.p.stoch_k,
            slowk_period=self.p.stoch_smooth,
            slowd_period=self.p.stoch_d,
        )
        adx = talib.ADX(h, lo, c, timeperiod=self.p.adx_period)

        if np.isnan(macd_hist[-2]) or np.isnan(k[-2]) or np.isnan(adx[-1]):
            return

        hist_cross_up = macd_hist[-2] <= 0 < macd_hist[-1]
        hist_cross_dn = macd_hist[-2] >= 0 > macd_hist[-1]
        stoch_up = k[-2] < self.p.oversold <= k[-1] and k[-1] > d[-1]
        stoch_dn = k[-2] > self.p.overbought >= k[-1] and k[-1] < d[-1]
        trending = adx[-1] >= self.p.adx_min

        if not trending:
            return

        if hist_cross_up and stoch_up:
            self._enter(
                "long", price_c, atr,
                f"MACD hist +, stoch {k[-1]:.0f} up from oversold, ADX {adx[-1]:.0f}",
            )
        elif hist_cross_dn and stoch_dn:
            self._enter(
                "short", price_c, atr,
                f"MACD hist -, stoch {k[-1]:.0f} down from overbought, ADX {adx[-1]:.0f}",
            )


if __name__ == "__main__":  # self-check -- runs a strategy over a synthetic trend, no network
    import pandas as pd

    n = 400
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    trend = np.linspace(0, 40, n) + np.sin(np.linspace(0, 30, n)) * 3
    close = 100.0 + trend
    df = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )

    for StratCls in (TALibCandleStrategy, TALibMacdStochStrategy):
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data)
        cerebro.addstrategy(StratCls)
        results = cerebro.run()
        strat = results[0]
        assert isinstance(strat.trades, list)
        print(f"{StratCls.__name__}: {len(strat.trades)} trades on synthetic data")
    print("talib_strategies self-check ok")
