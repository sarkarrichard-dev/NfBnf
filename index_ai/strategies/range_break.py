"""Prior-day range failed-breakout ("liquidity sweep") reversal.

From a widely-shared reel (sumedhhkumar.ai). Mechanics, per side:

    1. Mark the previous day's high (D1H) and low (D1L).
    2. Break — an intraday candle CLOSES beyond the level by a buffer
       (`break_buffer` — the reel's "+10 pts", here a % of price).
    3. Signal — the first opposite-colour candle after the break
       (green after a down-break of D1L, red after an up-break of D1H).
    4. Entry — a later candle takes out the signal candle's far edge in the
       reversal direction (breaks the green's HIGH -> long; the red's LOW -> short).
    5. Stop — the signal candle's near edge (green's LOW / red's HIGH).
    6. Target — the *opposite* prior-day extreme (D1H for a long, D1L for a short).

One position per side per day. If the entry trigger fails, re-arm on the next
opposite-colour candle until `entry_cutoff`. Pure — `replay_day` takes one day's
intraday frame and returns 0-2 fills. No look-ahead: entries and the target fill
at the next bar's open, a stop fills at the stop level intrabar.

**Backtest verdict (2026-09-11): no edge.** Replayed on NIFTY / BANKNIFTY /
SENSEX futures (spot proxy, 1m and 5m, 2017-2026) and MCX crude / gas / gold /
silver (5m, 90d), across the buffer / target-cap / cutoff grid. Net-negative on
**every** instrument and **every** config — win rate lands just short of
breakeven whatever the target (~34% at 2:1, ~29% at 3:1). The premise —
"a sweep of the prior-day extreme reliably reverses to the opposite extreme" —
happens ~1 in 20 sweeps, not enough to cover the failures. Kept as research
tooling (`scripts/backtest_range_break.py`), not wired to any lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RangeBreakConfig:
    break_buffer_pct: float = 0.04  # candle must close this % beyond D1H/D1L to count as a break
    entry_cutoff: time = time(14, 30)  # no NEW setups armed after this (IST)
    both_sides: bool = True  # arm buy (D1L sweep) and sell (D1H sweep) the same day
    min_rr: float = 0.0  # skip an entry whose target is < this * risk (0 = take all)
    max_target_rr: float = 0.0  # cap the target at this * risk (0 = full D1 extreme)


def _is_green(row: Any) -> bool:
    return float(row["close"]) > float(row["open"])


def replay_day(
    d1_high: float, d1_low: float, intraday: pd.DataFrame, cfg: RangeBreakConfig
) -> list[dict[str, Any]]:
    """`intraday` is one session, columns datetime/open/high/low/close, time-sorted."""
    if intraday.empty or d1_high <= d1_low:
        return []
    df = intraday.reset_index(drop=True)
    ts = pd.to_datetime(df["datetime"])
    buf_hi = d1_high * (1 + cfg.break_buffer_pct / 100.0)
    buf_lo = d1_low * (1 - cfg.break_buffer_pct / 100.0)

    fills: list[dict[str, Any]] = []
    # per-side state machine: 'wait_break' -> 'wait_signal' -> 'wait_entry' -> 'in'/'done'
    sides = {
        "long": {"state": "wait_break", "sig_hi": 0.0, "sig_lo": 0.0},
        "short": {"state": "wait_break", "sig_hi": 0.0, "sig_lo": 0.0},
    }
    if not cfg.both_sides:
        pass  # both armed anyway; caller can filter fills by direction

    pos: dict[str, Any] | None = None

    for i in range(len(df) - 1):
        row = df.iloc[i]
        nxt = df.iloc[i + 1]
        t = ts.iloc[i].time()
        hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])

        # ---- manage an open position (fill at this bar) ----
        if pos:
            d = 1 if pos["dir"] == "long" else -1
            hit_stop = (lo <= pos["stop"]) if d == 1 else (hi >= pos["stop"])
            hit_tp = (hi >= pos["target"]) if d == 1 else (lo <= pos["target"])
            if hit_stop and hit_tp:  # ambiguous bar — assume stop first (conservative)
                hit_tp = False
            if hit_stop or hit_tp:
                exit_px = pos["stop"] if hit_stop else pos["target"]
                fills.append(
                    {
                        **pos,
                        "exit": exit_px,
                        "exit_time": str(ts.iloc[i]),
                        "reason": "stop" if hit_stop else "target",
                        "points": (exit_px - pos["entry"]) * d,
                    }
                )
                pos = None
            continue

        # ---- work each side ----
        for name, s in sides.items():
            if s["state"] in ("in", "done"):
                continue
            up = name == "short"  # short side watches an UP-break of D1H

            if s["state"] == "wait_break":
                if (cl > buf_hi) if up else (cl < buf_lo):
                    s["state"] = "wait_signal"
                continue

            if t >= cfg.entry_cutoff:
                s["state"] = "done"
                continue

            if s["state"] == "wait_signal":
                # signal = first opposite-colour candle (red after up-break / green after down-break)
                if (not _is_green(row)) if up else _is_green(row):
                    s.update(state="wait_entry", sig_hi=hi, sig_lo=lo)
                continue

            if s["state"] == "wait_entry":
                trig = (lo < s["sig_lo"]) if up else (hi > s["sig_hi"])
                void = (hi > s["sig_hi"]) if up else (lo < s["sig_lo"])  # went the wrong way first
                if trig:
                    entry = s["sig_lo"] if up else s["sig_hi"]
                    stop = s["sig_hi"] if up else s["sig_lo"]
                    risk = abs(entry - stop)
                    tgt = d1_low if up else d1_high
                    if cfg.max_target_rr > 0 and risk > 0:
                        capped = (
                            entry - cfg.max_target_rr * risk
                            if up
                            else entry + cfg.max_target_rr * risk
                        )
                        tgt = max(tgt, capped) if up else min(tgt, capped)
                    rr = abs(tgt - entry) / risk if risk > 0 else 0.0
                    if risk > 0 and rr >= cfg.min_rr:
                        pos = {
                            "dir": "short" if up else "long",
                            "entry": float(nxt["open"]),  # next-bar open, no look-ahead
                            "stop": stop,
                            "target": tgt,
                            "risk": risk,
                            "rr": round(rr, 2),
                            "entry_time": str(ts.iloc[i + 1]),
                        }
                    s["state"] = "in" if pos else "done"
                elif void:
                    s["state"] = "wait_signal"  # re-arm on the next opposite-colour candle

    # close any runner at the last bar
    if pos:
        last = df.iloc[-1]
        d = 1 if pos["dir"] == "long" else -1
        fills.append(
            {
                **pos,
                "exit": float(last["close"]),
                "exit_time": str(ts.iloc[-1]),
                "reason": "eod",
                "points": (float(last["close"]) - pos["entry"]) * d,
            }
        )
    return fills


if __name__ == "__main__":  # self-check — a clean down-sweep of D1L that reverses to D1H
    rows = []
    # flat open near 100.5, then a sweep to 99.4 (below D1L 100), a green bounce,
    # entry on the break of the green high, run up to D1H 102.
    seq = [100.5, 100.3, 99.9, 99.5, 99.4, 99.8, 100.2, 100.6, 101.1, 102.3, 102.4, 102.2]
    for k, c in enumerate(seq):
        o = seq[k - 1] if k else c
        rows.append(
            {
                "datetime": pd.Timestamp("2026-06-01 09:15") + pd.Timedelta(minutes=5 * k),
                "open": o,
                "high": max(o, c) + 0.15,
                "low": min(o, c) - 0.15,
                "close": c,
            }
        )
    df = pd.DataFrame(rows)
    fills = replay_day(102.0, 100.0, df, RangeBreakConfig(break_buffer_pct=0.1))
    assert fills and fills[0]["dir"] == "long", fills
    assert fills[0]["reason"] == "target", fills[0]
    print("range_break self-check ok —", fills[0]["reason"], "rr", fills[0]["rr"])
