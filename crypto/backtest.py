"""Replay both crypto strategies over real Delta Exchange candle history.

Perps have real historical prices (unlike the index options backtest, which
uses a Black-Scholes proxy), so the relative *and* rough absolute P&L here are
trustworthy — once the fee/half-spread from ``crypto.charges`` is applied. The
half-spread is still the bps fallback until Phase 3's measurement path has run
for a while, so treat absolute rupee figures as approximate.

    python -m crypto.backtest --days 120
    python -m crypto.backtest --days 60 --asset BTCUSD --strategy ny_n_break
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.charges import round_trip_cost_usd
from crypto.config import PERP_SYMBOLS, crypto_settings
from crypto.delta import market_data, products
from crypto.delta.products import Contract
from crypto.session import in_ny_window, ny_session_date
from crypto.sizing import lots_from_margin_budget, size_position
from crypto.strategies import (
    ak_roxx_pro,
    bb_reversal,
    ema_jaguar,
    ichimoku as ichi,
    ny_n_break as nb,
    tma_phoenix,
    vp_edge,
)
from crypto.strategies.trailing import TrailConfig

# name -> (module, timeframe (str, or a settings->str fn), cfg factory taking
# (settings, **overrides)). Every plain step(sym, candles, *, state, cfg)
# strategy runs through backtest_simple; ny_n_break is the only bespoke one
# (needs 5m + 15m + session flags).
_SIMPLE = {
    "ichimoku": (
        ichi,
        lambda s: s.ichimoku_tf,
        lambda s, **kw: ichi.IchimokuConfig(trail=_trail(s), **kw),
    ),
    "bb_reversal": (
        bb_reversal,
        "5m",
        lambda s, **kw: bb_reversal.BBReversalConfig(trail=_trail(s), **kw),
    ),
    "ema_jaguar": (
        ema_jaguar,
        "5m",
        lambda s, **kw: ema_jaguar.EmaJaguarConfig(trail=_trail(s), **kw),
    ),
    "vp_edge": (vp_edge, "15m", lambda s, **kw: vp_edge.VpEdgeConfig(trail=_trail(s), **kw)),
    "ak_roxx_pro": (
        ak_roxx_pro,
        lambda s: ak_roxx_pro.AkRoxxConfig().timeframe,
        lambda s, **kw: ak_roxx_pro.AkRoxxConfig(trail=_trail(s), **kw),
    ),
    "tma_phoenix": (
        tma_phoenix,
        "5m",
        lambda s, **kw: tma_phoenix.TmaPhoenixConfig(trail=_trail(s), **kw),
    ),
}
_WIN_N = {"ichimoku": 220, "ak_roxx_pro": 60, "tma_phoenix": 340}  # ak_roxx: 34 EMA + prior hour
ALL_STRATEGIES = ["ny_n_break", *_SIMPLE]


def _trail(s) -> TrailConfig:
    return TrailConfig(
        leverage=s.leverage,
        stop_pnl_pct=s.stop_pnl_pct,
        ratchet_step_pnl_pct=s.ratchet_step_pnl_pct,
        tp_trigger_pnl_pct=s.tp_trigger_pnl_pct,
        peak_trail_pnl_pct=s.peak_trail_pnl_pct,
    )


# Delta perpetual contract values (units of coin per contract). Used only when
# the live contract master is unreachable; the real values come from the API.
_FALLBACK_CV = {"BTCUSD": 0.001, "ETHUSD": 0.01}
_WINDOW = 220  # trailing bars handed to step() — enough for EMA25 / 15m swings / a warm cloud


@dataclass
class Trade:
    strategy: str
    asset: str
    side: str
    entry_ts: str
    exit_ts: str
    entry: float
    exit: float
    size: int
    pnl_usd: float
    reason: str


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return _summarise(self.trades)


def _contract(sym: str) -> Contract:
    try:
        c = products.get(sym)
        if c and c.usable:
            return c
    except Exception:
        pass
    return Contract(sym, 0, _FALLBACK_CV.get(sym, 0.001), 0.5, 1, 100)


def _summarise(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    out = {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3),
        "net_usd": round(sum(pnls), 2),
        "avg_win_usd": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss_usd": round(statistics.mean(losses), 2) if losses else 0.0,
        "max_drawdown_usd": round(max_dd, 2),
        "by": {},
    }
    for key in sorted({(t.strategy, t.asset) for t in trades}):
        sub = [t.pnl_usd for t in trades if (t.strategy, t.asset) == key]
        out["by"][f"{key[0]}:{key[1]}"] = {
            "trades": len(sub),
            "win_rate": round(sum(1 for p in sub if p > 0) / len(sub), 3),
            "net_usd": round(sum(sub), 2),
        }
    return out


def _record_exit(trades, strat, sym, pos, exit_px, exit_ts, reason, s, contract):
    lots = lots_from_margin_budget(
        contract, pos["entry"], margin_usd=s.margin_per_position_usd, leverage=s.leverage
    )
    sr = size_position(
        contract,
        pos["entry"],
        lots=lots,
        deploy_usd=s.deploy_usd,
        leverage=s.leverage,
        wallet_usd=s.paper_bankroll_usd,
    )
    size = sr.size if sr.ok else 1
    notional = size * contract.contract_value * pos["entry"]
    direction = 1 if pos["side"] == "long" else -1
    coins = size * contract.contract_value
    gross = (exit_px - pos["entry"]) * direction * coins
    cost = round_trip_cost_usd(notional, sym, exit_px, size, contract.contract_value)
    trades.append(
        Trade(
            strat,
            sym,
            pos["side"],
            pos["entry_ts"],
            str(exit_ts),
            pos["entry"],
            exit_px,
            size,
            round(gross - cost, 4),
            reason,
        )
    )


def backtest_ny_n_break(sym: str, days: float, s) -> list[Trade]:
    contract = _contract(sym)
    c5 = market_data.candles(sym, "5m", days=days)
    if len(c5) < _WINDOW + 10:
        return []
    c15_all = market_data.resample(c5, "15min")
    ny_start, ny_end = s.ny_start, s.ny_end
    state: dict[str, Any] | None = None
    open_pos: dict[str, Any] | None = None
    trades: list[Trade] = []

    for i in range(_WINDOW, len(c5)):
        win5 = c5.iloc[i - _WINDOW : i + 1].reset_index(drop=True)
        now = pd.Timestamp(win5["datetime"].iloc[-1]).to_pydatetime()
        win15 = (
            c15_all[c15_all["datetime"] <= win5["datetime"].iloc[-1]]
            .tail(200)
            .reset_index(drop=True)
        )
        allround = getattr(s, "nbreak_allround", False)
        state, ev = nb.step(
            sym,
            win5,
            win15,
            state=state,
            cfg=nb.NBreakConfig(trail=_trail(s)),
            in_session=True if allround else in_ny_window(ny_start, ny_end, now),
            session_date=now.date().isoformat()
            if allround
            else ny_session_date(ny_start, ny_end, now),
        )
        px = float(win5["close"].iloc[-1])
        ts = win5["datetime"].iloc[-1]
        if ev["event"] == "enter":
            open_pos = {"side": ev["side"], "entry": px, "entry_ts": str(ts)}
        elif ev["event"] == "exit" and open_pos:
            _record_exit(
                trades, "ny_n_break", sym, open_pos, px, ts, ev.get("reason", ""), s, contract
            )
            open_pos = None
    return trades


def backtest_simple(
    name: str,
    sym: str,
    days: float,
    s,
    *,
    cfg_overrides: dict | None = None,
    frame: pd.DataFrame | None = None,
) -> list[Trade]:
    """Generic replay for a strategy with the plain step(sym, candles, *, state, cfg)
    shape (ichimoku / bb_reversal / ema_jaguar / vp_edge / ak_roxx_pro).
    ``frame`` overrides the fetched candles (walk-forward optimiser, one fold)."""
    module, tf, make_cfg = _SIMPLE[name]
    tf = tf(s) if callable(tf) else tf
    contract = _contract(sym)
    fr = frame if frame is not None else market_data.candles(sym, tf, days=days)
    win_n = _WIN_N.get(name, 160)
    if len(fr) < win_n + 10:
        return []
    cfg = make_cfg(s, **(cfg_overrides or {}))
    state: dict[str, Any] | None = None
    open_pos: dict[str, Any] | None = None
    trades: list[Trade] = []
    for i in range(win_n, len(fr)):
        win = fr.iloc[i - win_n : i + 1].reset_index(drop=True)
        state, ev = module.step(sym, win, state=state, cfg=cfg)
        px = float(win["close"].iloc[-1])
        ts = win["datetime"].iloc[-1]
        if ev["event"] == "enter":
            open_pos = {"side": ev["side"], "entry": px, "entry_ts": str(ts)}
        elif ev["event"] == "exit" and open_pos:
            _record_exit(trades, name, sym, open_pos, px, ts, ev.get("reason", ""), s, contract)
            open_pos = None
    return trades


def run(
    days: float = 120, assets: list[str] | None = None, strategies: list[str] | None = None
) -> Result:
    s = crypto_settings()
    assets = assets or list(PERP_SYMBOLS)
    strategies = strategies or ["ny_n_break", "ichimoku"]
    res = Result()
    for sym in assets:
        if "ny_n_break" in strategies:
            res.trades += backtest_ny_n_break(sym, days, s)
        for name in _SIMPLE:
            if name in strategies:
                res.trades += backtest_simple(name, sym, days, s)
    return res


def _main() -> None:
    ap = argparse.ArgumentParser(description="Replay crypto strategies over Delta history")
    ap.add_argument("--days", type=float, default=120)
    ap.add_argument("--asset", action="append", choices=list(PERP_SYMBOLS))
    ap.add_argument("--strategy", action="append", choices=ALL_STRATEGIES)
    args = ap.parse_args()
    res = run(args.days, args.asset, args.strategy)
    summary = res.summary()
    print(f"\nCrypto backtest — {args.days:g} days, {summary.get('trades', 0)} trades")
    if not summary.get("trades"):
        print("  (no trades / not enough history)")
        return
    print(
        f"  net ${summary['net_usd']:,.2f}  ·  win rate {summary['win_rate']:.0%}  "
        f"({summary['wins']}W / {summary['losses']}L)  ·  max DD ${summary['max_drawdown_usd']:,.2f}"
    )
    print(f"  avg win ${summary['avg_win_usd']:,.2f}  ·  avg loss ${summary['avg_loss_usd']:,.2f}")
    for k, v in summary["by"].items():
        print(
            f"    {k:<24} {v['trades']:>4} trades  net ${v['net_usd']:>10,.2f}  win {v['win_rate']:.0%}"
        )
    print("\n  Note: half-spread is the bps fallback until the measurement path has data;")
    print("  absolute figures are approximate, relative comparisons are sound.")


if __name__ == "__main__":
    _main()
