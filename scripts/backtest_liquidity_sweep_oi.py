"""Liquidity-sweep reversal for MCX commodities, confirmed by real Open
Interest and a volume-profile "thin zone" gate.

Richard, 2026-09-15: asked whether the platform understands liquidity sweeps,
order flow, and volume profile, then asked to build something combining them
for futures/commodities — a section that hasn't had a strategy proposal of
its own yet (it only reuses the Indian index directional signal).

**This is a variant of a strategy already tested and explicitly killed.** On
2026-09-11 a plain "mark yesterday's high/low, fade the sweep, target the
opposite extreme" strategy was backtested on NIFTY/BANKNIFTY/SENSEX futures
(2017-2026) and all four MCX commodities and came back net-negative on every
buffer/target/cutoff setting tried; Richard had the code deleted and said not
to rebuild it (see strategy-findings.md). The plain version had nothing
behind the sweep except price action. This version is deliberately different:
it only takes the fade when Open Interest confirms the move was unwinding
(stop-driven, finite) rather than fresh conviction (real, likely to
continue), and only when the sweep pushes into a price the volume profile
shows was thinly traded (a low-resistance zone to snap back through). The
honest prior is still "probably no edge" — the plain version failed *every*
setting — but this tests a genuinely different question: does real
confirmation change that verdict.

**Scope: MCX commodities only, not Indian index/stock futures.** Those two
paper lanes trade the cash/spot price as a proxy for the future (the near-
month basis is small and decays to zero by expiry — see futures/paper.py),
not the actual futures contract, so there is no real Open Interest to read
for what they're actually fetching. Commodities trade the real MCX futures
contract (FUTCOM), which does carry real, continuously-varying OI (confirmed
live against Dhan, 2026-09-15 — full history back to numbers Dhan itself
returns, not just the current front-month contract's own life). Extending
this idea to index/stock futures would first need each instrument's actual
futures-contract security id resolved (they aren't today) — a separate,
larger piece of work, not attempted here.

Signal (15m candles + real OI, per commodity):
  1. Sweep: this bar's high breaks yesterday's high but closes back under it
     (bearish fade candidate), or this bar's low breaks yesterday's low but
     closes back above it (bullish fade candidate).
  2. OI confirmation: over the sweep bar and the two before it, OI must be
     FALLING for a fade to qualify — a downside sweep + falling OI reads as
     long unwinding (existing longs stopping out, not fresh shorts), an
     upside sweep + falling OI reads as short covering. Rising OI on the
     sweep means fresh conviction pushing the move — skip it, don't fade a
     real breakout.
  3. Volume-profile gate: the sweep price must sit outside the value area
     (VAH/VAL) of the last 96 bars (~1 trading day) — a level the profile
     shows was thinly traded, not a level with real acceptance.
  Exit: stop just beyond the sweep bar's own extreme (the fade thesis is
  wrong if that gets taken out); target the profile's POC (a real "snap back
  to fair value" level, not an arbitrary opposite extreme); a 48-bar (~12h)
  time-based exit if neither is hit.

    python -m scripts.backtest_liquidity_sweep_oi
    python -m scripts.backtest_liquidity_sweep_oi --only CRUDEOILM --days 200
    python -m scripts.backtest_liquidity_sweep_oi --selfcheck        # no network
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from commodities.charges import round_trip_cost_rupees, slippage_rupees
from commodities.instruments import BY_KEY, candle_instrument, load_universe_meta
from crypto.strategies.volprofile import profile
from index_ai.config import settings
from index_ai.dhan import DhanClient, chart_response_to_frame

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 85  # Dhan /charts/intraday serves ~90 calendar days per request
PROFILE_LOOKBACK = 96  # ~1 trading day of 15m bars
OI_LOOKBACK = 3  # bars checked for the OI direction at the sweep
MAX_HOLD_BARS = 48  # ~12h at 15m — a mean-reversion setup shouldn't need longer


@dataclass
class Trade:
    symbol: str
    side: str
    entry_ts: str
    exit_ts: str
    entry: float
    exit: float
    reason: str
    net_rupees: float


def _fetch_with_oi(client: DhanClient, key: str, security_id: int, days: float) -> pd.DataFrame:
    """Chunked 15m fetch with real OI, oldest-first, de-duplicated."""
    inst = candle_instrument(BY_KEY[key], security_id)
    now = datetime.now(IST)
    start_limit = now - timedelta(days=days)
    frames: list[pd.DataFrame] = []
    end = now
    while end > start_limit:
        start = max(start_limit, end - timedelta(days=WINDOW_DAYS))
        try:
            raw = client.intraday_history(
                inst,
                from_date=start.strftime("%Y-%m-%d 09:00:00"),
                to_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                interval="15",
                include_oi=True,
            )
            fr = chart_response_to_frame(raw)
            if not fr.empty:
                frames.append(fr)
        except Exception as exc:  # noqa: BLE001 — keep walking past a bad window
            print(f"  {key} {start.date()}..{end.date()} failed: {exc}")
        end = start - timedelta(seconds=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("datetime").sort_values("datetime")
    return out.reset_index(drop=True)


def _sessions(df: pd.DataFrame) -> dict[Any, pd.DataFrame]:
    work = df.copy()
    work["_d"] = pd.to_datetime(work["datetime"]).dt.date
    return {d: g.drop(columns="_d").reset_index(drop=True) for d, g in work.groupby("_d")}


def _oi_falling(oi: pd.Series) -> bool:
    """OI strictly lower now than OI_LOOKBACK bars ago -- unwinding, not buildup."""
    if len(oi) <= OI_LOOKBACK:
        return False
    return float(oi.iloc[-1]) < float(oi.iloc[-1 - OI_LOOKBACK])


def backtest_one(key: str, security_id: int, days: float, client: DhanClient) -> list[Trade]:
    df = _fetch_with_oi(client, key, security_id, days)
    if df.empty:
        return []
    return walk_signal(key, df)


def walk_signal(key: str, df: pd.DataFrame) -> list[Trade]:
    """Pure: candles + open_interest in, trades out -- no network. The part
    self-checked offline; backtest_one is just this plus the real Dhan fetch."""
    spec = BY_KEY[key]
    by_day = _sessions(df)
    days_sorted = sorted(by_day)
    trades: list[Trade] = []
    open_pos: dict[str, Any] | None = None

    for i in range(1, len(days_sorted)):
        prev_day, today_day = by_day[days_sorted[i - 1]], by_day[days_sorted[i]]
        if len(prev_day) < 5 or len(today_day) < 5:
            continue
        y_hi, y_lo = float(prev_day["high"].max()), float(prev_day["low"].min())
        full = pd.concat([prev_day, today_day], ignore_index=True)
        offset = len(prev_day)

        for j in range(max(offset, PROFILE_LOOKBACK), len(full)):
            row = full.iloc[j]
            ts = str(row["datetime"])
            price = float(row["close"])

            if open_pos:
                # wall-clock elapsed, not a bar-index difference: entry_idx and j
                # are indices into two DIFFERENT per-day-pair `full` frames once a
                # position survives a day boundary (each outer iteration rebuilds
                # `full` from scratch), so they are not comparable -- an index
                # subtraction here silently understated elapsed time and let a
                # position ride for an extra day past the intended 12h cap
                # (caught 2026-09-15 via a real GOLDM trade that held overnight).
                held = pd.Timestamp(ts) - pd.Timestamp(open_pos["entry_ts"])
                d = 1 if open_pos["side"] == "long" else -1
                hit_stop = (price <= open_pos["stop"]) if d == 1 else (price >= open_pos["stop"])
                hit_target = (price >= open_pos["target"]) if d == 1 else (price <= open_pos["target"])
                if hit_stop or hit_target or held >= timedelta(minutes=15 * MAX_HOLD_BARS):
                    reason = "stop" if hit_stop else "target" if hit_target else "time"
                    exit_px = open_pos["stop"] if hit_stop else open_pos["target"] if hit_target else price
                    gross = (exit_px - open_pos["entry"]) * d * spec.multiplier
                    cost = round_trip_cost_rupees(open_pos["entry"], exit_px, spec, 1) + slippage_rupees(spec, 1)
                    trades.append(
                        Trade(key, open_pos["side"], open_pos["entry_ts"], ts,
                              open_pos["entry"], exit_px, reason, round(gross - cost, 2))
                    )
                    open_pos = None
                continue

            hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
            win = full.iloc[max(0, j - PROFILE_LOOKBACK) : j + 1]
            prof = profile(win, bins=24)
            if prof is None:
                continue
            oi_win = full["open_interest"].iloc[max(0, j - OI_LOOKBACK) : j + 1]

            bearish_sweep = hi > y_hi and cl < y_hi
            bullish_sweep = lo < y_lo and cl > y_lo

            if bearish_sweep and _oi_falling(oi_win) and hi > prof.vah:
                open_pos = {
                    "side": "short", "entry": cl, "entry_ts": ts,
                    "stop": hi * 1.001, "target": prof.poc,
                }
            elif bullish_sweep and _oi_falling(oi_win) and lo < prof.val:
                open_pos = {
                    "side": "long", "entry": cl, "entry_ts": ts,
                    "stop": lo * 0.999, "target": prof.poc,
                }
    return trades


def _summarise(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnls = [t.net_rupees for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3),
        "net_rupees": round(sum(pnls), 2),
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
    }


def _selfcheck() -> None:
    """No network: a calm settled day (real value area + POC), then a sweep
    bar poking above it on falling OI -- must fire a short. Mirror the
    setup with a bullish sweep on rising OI -- must NOT fire (fresh
    conviction, not a fade). Then walk the short to its stop."""
    # exactly 96 bars = 24h, so this whole block sits inside one calendar day
    # (2026-01-01) and PROFILE_LOOKBACK's own 96-bar window is fully covered
    # by the time the spike day starts -- both requirements _sessions() and
    # the j-range in walk_signal need to actually reach the spike bars.
    n_calm = PROFILE_LOOKBACK
    calm = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01 00:00", periods=n_calm, freq="15min"),
            "open": [100.0] * n_calm, "high": [100.3] * n_calm, "low": [99.7] * n_calm,
            "close": [100.0] * n_calm, "volume": [50.0] * n_calm,
            "open_interest": [10000.0] * n_calm,
        }
    )
    # day 2: a sweep bar (high pokes above the calm day's high, closes back
    # under) with OI falling over the prior few bars -- should fire a short
    spike = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 00:00", periods=6, freq="15min"),
            "open": [100.0, 100.1, 103.5, 100.2, 99.5, 99.0],
            "high": [100.3, 100.4, 104.0, 100.5, 99.8, 99.3],
            "low": [99.7, 99.8, 100.0, 99.8, 99.0, 98.5],
            "close": [100.1, 100.2, 100.1, 99.6, 99.2, 98.8],
            "volume": [50.0] * 6,
            "open_interest": [9800.0, 9500.0, 9200.0, 9100.0, 9000.0, 8900.0],
        }
    )
    df = pd.concat([calm, spike], ignore_index=True)
    trades = walk_signal("CRUDEOILM", df)
    assert trades, "expected the sweep + falling-OI setup to fire a trade"
    t = trades[0]
    assert t.side == "short", t
    assert t.reason in ("stop", "target", "time"), t
    print(f"  short fired: entry {t.entry}, exit {t.exit} ({t.reason}), net Rs{t.net_rupees:,.2f}")

    # mirror: same sweep shape, but OI RISING (fresh conviction) -- must not fire
    spike_rising_oi = spike.assign(
        open_interest=[9800.0, 10100.0, 10400.0, 10600.0, 10800.0, 11000.0]
    )
    df2 = pd.concat([calm, spike_rising_oi], ignore_index=True)
    trades2 = walk_signal("CRUDEOILM", df2)
    assert not trades2, f"rising OI on the sweep should skip the fade, got {trades2}"
    print("scripts.backtest_liquidity_sweep_oi self-check ok")


def _main() -> None:
    ap = argparse.ArgumentParser(description="Liquidity-sweep + OI + volume-profile, MCX commodities")
    ap.add_argument("--days", type=float, default=500)
    ap.add_argument("--only", action="append", choices=list(BY_KEY))
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return

    meta = load_universe_meta()
    keys = args.only or list(BY_KEY)
    client = DhanClient(settings().dhan)

    all_trades: list[Trade] = []
    print(f"\nLiquidity sweep + OI + volume profile - {args.days:g} days, MCX commodities")
    for key in keys:
        m = meta.get(key)
        if not m or not m.get("security_id"):
            print(f"  {key}: no resolved security id (run scripts.fetch_commodity_universe)")
            continue
        trades = backtest_one(key, int(m["security_id"]), args.days, client)
        all_trades += trades
        s = _summarise(trades)
        if s["trades"]:
            print(f"  {key:<12} {s['trades']:>4} trades  net Rs{s['net_rupees']:>10,.2f}  "
                  f"win {s['win_rate']:.0%}  avg win Rs{s['avg_win']:,.0f}  avg loss Rs{s['avg_loss']:,.0f}")
        else:
            print(f"  {key:<12}    0 trades")

    total = _summarise(all_trades)
    print(f"\n  TOTAL: {total.get('trades', 0)} trades", end="")
    if total.get("trades"):
        print(f"  net Rs{total['net_rupees']:,.2f}  win rate {total['win_rate']:.0%}")
    else:
        print()
    print("\n  Real MCX charges + 1-tick slippage each side. Real OI from Dhan (oi=true).")


if __name__ == "__main__":
    _main()
