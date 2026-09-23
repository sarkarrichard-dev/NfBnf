"""
Strategy lab: paper-trade candidate option strategies side by side on the
real recorded option chain (``market_log.chain``), each scored on its own per
index, net of real Dhan charges.

How a lab trade is priced — deliberately the pessimistic, honest way:
  * enter/exit on the snapshot's real quotes: buy at the ask, sell at the bid
    (so the bid-ask spread is paid on every leg, not estimated);
  * charges per executed leg from ``index_ai.charges.leg_charge_rupees`` —
    ₹20 brokerage + STT + exchange + SEBI + GST + stamp;
  * 1 lot; one open position per (strategy, index); at most 2 trades a day;
    entries 09:30-14:30, everything closed by 15:10 (intraday only).

Signals come from ``oi_signals.read`` computed with only the snapshots known at
that moment, so replaying a recorded day gives the same trades live paper would.
Nothing here places an order or changes a running strategy.

Verdict per (strategy, index), on Richard's readiness bar:
  COLLECTING  fewer than 30 trades or 14 trading days — no call yet
  DROPPED     enough data and net-negative after charges
  PASSING     enough data and net-positive — a candidate to discuss, not to arm
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from index_ai import market_log
from index_ai.charges import leg_charge_rupees
from index_ai.instruments import market_lot_size
from index_ai.strategies import oi_signals
from index_ai.strategies.candlestick_sr import intraday_candle_trend

ENTRY_FROM, ENTRY_UNTIL, SQUARE_OFF = "09:30", "14:30", "15:10"
MAX_TRADES_PER_DAY = 2
HEDGE_STRIKES = 4  # long leg sits 4 strikes beyond the short one
SPREAD_TARGET, SPREAD_STOP = 0.5, 1.0  # keep 50% of the credit / lose 1x the credit
BUY_TARGET, BUY_STOP = 0.30, 0.20  # +30% / -20% of premium paid
MIN_TRADES, MIN_DAYS = 30, 14
PA_BARS = 6        # today's own 5m candles the structure read looks at (30 min)

Direction = Callable[[dict[str, Any]], int]  # signal read -> +1 up, -1 down, 0 none


def _bias(sig: dict[str, Any]) -> int:
    return {"BULLISH": 1, "BEARISH": -1}.get(sig["bias"], 0)


def _writing(sig: dict[str, Any]) -> int:
    v = sig["votes"]
    return v["writing"] if v["writing"] and v["walls"] != -v["writing"] else 0


def _structure(sig: dict[str, Any]) -> int:
    return {"UP": 1, "DOWN": -1}.get(sig.get("structure"), 0)


def _fade(sig: dict[str, Any]) -> int:
    return -_structure(sig)


# name -> (lane, direction rule, plain description)
CANDIDATES: dict[str, tuple[str, Direction, str]] = {
    "oi_bias_spread": ("sell", _bias, "Credit spread in the direction of the combined OI bias"),
    "oi_writing_spread": (
        "sell",
        _writing,
        "Credit spread with the side option writers are adding to",
    ),
    "oi_bias_buy": ("buy", _bias, "Buy the at-the-money option in the direction of the OI bias"),
    # Price action from TODAY's own candles only -- the live sell lane's 5m
    # tape spans yesterday's bars until ~10:30. Two opposite readings, because
    # the first 30 live sells hinted that selling *against* the structure did
    # better than with it; the lab decides, not a 30-trade hunch.
    "pa_structure_spread": (
        "sell",
        _structure,
        "Credit spread with today's 5m price structure (higher highs/lows = bull put); none = no trade",
    ),
    "pa_fade_spread": (
        "sell",
        _fade,
        "Credit spread against today's 5m price structure (sell into the move); none = no trade",
    ),
}


@dataclass
class _Pos:
    legs: list[tuple[float, str, str, float]]  # (strike, CE/PE, BUY/SELL, entry price)
    entered: str
    direction: int
    basis: float  # credit received / premium paid, points
    charges: float = 0.0


def _quotes(snap: oi_signals.Snapshot) -> dict[tuple[float, str], dict[str, Any]]:
    return {(r["strike"], r["opt_type"]): r for r in snap}


def _fill(q: dict[str, Any] | None, side: str) -> float | None:
    """Price we'd actually get: buy at ask, sell at bid. None if no real quote."""
    if not q:
        return None
    p = q.get("ask") if side == "BUY" else q.get("bid")
    return float(p) if p and p > 0 else None


def _open(
    lane: str, direction: int, snap: oi_signals.Snapshot
) -> list[tuple[float, str, str]] | None:
    spot = float(snap[0]["spot"] or 0)
    strikes = sorted({r["strike"] for r in snap})
    if lane == "buy":
        atm = min(strikes, key=lambda k: abs(k - spot))
        return [(atm, "CE" if direction > 0 else "PE", "BUY")]
    if direction > 0:  # bull put spread
        below = [k for k in strikes if k < spot]
        if len(below) <= HEDGE_STRIKES:
            return None
        return [(below[-1], "PE", "SELL"), (below[-1 - HEDGE_STRIKES], "PE", "BUY")]
    above = [k for k in strikes if k > spot]  # bear call spread
    if len(above) <= HEDGE_STRIKES:
        return None
    return [(above[0], "CE", "SELL"), (above[HEDGE_STRIKES], "CE", "BUY")]


def _flip(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


def _points(pos: _Pos, prices: list[float]) -> float:
    """P&L in index points per unit, using exit prices for each leg."""
    return sum(
        (p - e) if side == "BUY" else (e - p) for (_, _, side, e), p in zip(pos.legs, prices)
    )


def _exit_prices(pos: _Pos, q: dict) -> list[float] | None:
    out = []
    for strike, typ, side, _ in pos.legs:
        p = _fill(q.get((strike, typ)), _flip(side))
        if p is None:
            return None
        out.append(p)
    return out


def _hit(lane: str, pos: _Pos, pts: float) -> str | None:
    if lane == "sell":
        if pts >= SPREAD_TARGET * pos.basis:
            return "target"
        if pts <= -SPREAD_STOP * pos.basis:
            return "stop"
    else:
        if pts >= BUY_TARGET * pos.basis:
            return "target"
        if pts <= -BUY_STOP * pos.basis:
            return "stop"
    return None


def _bars_5m(instrument: str, session: str) -> pd.DataFrame:
    """Today's 5m OHLC for the index from the real recorded ticks (09:15 on)."""
    with market_log.connect() as db:
        rows = db.execute(
            "SELECT ts, ltp FROM ticks WHERE session=? AND instrument=? AND ltp > 0 "
            "AND substr(ts, 12, 5) >= '09:15' AND substr(ts, 12, 5) < '15:30'",
            (session, instrument.upper()),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["high", "low", "end"])
    px = pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows]))
    bars = px.resample("5min", origin="start_day", offset="15min").agg(["max", "min"]).dropna()
    bars.columns = ["high", "low"]
    bars["end"] = bars.index + pd.Timedelta(minutes=5)
    return bars


def signals(instrument: str, session: str,
            snaps: list[tuple[str, oi_signals.Snapshot]]) -> list[dict[str, Any] | None]:
    """OI read per snapshot, plus ``structure``: UP/DOWN/RANGE from today's
    completed 5m candles up to that moment (no peeking at the bar in progress)."""
    bars = _bars_5m(instrument, session)
    out: list[dict[str, Any] | None] = [None]
    for ts, snap in snaps[1:]:
        sig = oi_signals.read(snaps[0][1], snap, session)
        done = bars[bars["end"] <= pd.Timestamp(ts)] if len(bars) else bars
        sig["structure"] = intraday_candle_trend(done, lookback=PA_BARS)
        out.append(sig)
    return out


def run_session(
    name: str,
    instrument: str,
    session: str,
    snaps: list[tuple[str, oi_signals.Snapshot]] | None = None,
    sigs: list[dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Paper-trade one candidate through one recorded day. Returns closed trades."""
    lane, rule, _ = CANDIDATES[name]
    snaps = snaps if snaps is not None else oi_signals.load_session(instrument, session)
    if len(snaps) < 2:
        return []
    if sigs is None:
        sigs = signals(instrument, session, snaps)
    lot = market_lot_size(instrument)
    exchange = "BSE" if instrument.upper() == "SENSEX" else "NSE"
    cost = lambda price, side: leg_charge_rupees(price, lot, side, exchange=exchange)  # noqa: E731

    trades: list[dict[str, Any]] = []
    pos: _Pos | None = None
    for i in range(1, len(snaps)):
        ts, snap = snaps[i]
        hhmm = ts[11:16]
        q = _quotes(snap)
        closing = hhmm >= SQUARE_OFF
        if pos:
            prices = _exit_prices(pos, q)
            if prices is None and not closing:
                continue
            if prices is None:  # forced close with a missing quote:
                prices = [
                    float((q.get((k, t)) or {}).get("ltp") or e)  # last price, else flat
                    for k, t, _, e in pos.legs
                ]
            pts = _points(pos, prices)
            why = "square-off" if closing else _hit(lane, pos, pts)
            if why:
                pos.charges += sum(
                    cost(p, _flip(side)) for (_, _, side, _), p in zip(pos.legs, prices)
                )
                gross = round(pts * lot, 2)
                trades.append(
                    {
                        "strategy": name,
                        "instrument": instrument.upper(),
                        "session": session,
                        "entered": pos.entered,
                        "exited": ts,
                        "direction": pos.direction,
                        "legs": [f"{side} {k:g} {t}" for k, t, side, _ in pos.legs],
                        "basis_points": round(pos.basis, 2),
                        "exit_reason": why,
                        "gross": gross,
                        "charges": round(pos.charges, 2),
                        "net": round(gross - pos.charges, 2),
                    }
                )
                pos = None
            continue
        if len(trades) >= MAX_TRADES_PER_DAY or not (ENTRY_FROM <= hhmm <= ENTRY_UNTIL):
            continue
        direction = rule(sigs[i]) if sigs[i] else 0
        if not direction:
            continue
        legs = _open(lane, direction, snap)
        fills = [_fill(q.get((k, t)), side) for k, t, side in legs] if legs else None
        if not fills or None in fills:
            continue
        basis = (fills[0] - fills[1]) if lane == "sell" else fills[0]
        if basis <= 0:
            continue
        pos = _Pos(
            legs=[(k, t, side, p) for (k, t, side), p in zip(legs, fills)],
            entered=ts,
            direction=direction,
            basis=basis,
            charges=sum(cost(p, side) for (_, _, side), p in zip(legs, fills)),
        )
    # ponytail: a position still open when the day's recording stops (mid-session
    # view, or the server went down before 15:10) is not counted -- no fake exit.
    return trades


def run(instruments: list[str], sessions: list[str]) -> dict[str, Any]:
    """Every candidate x index x recorded day, plus a verdict per pair."""
    trades: list[dict[str, Any]] = []
    for inst in instruments:
        for session in sessions:
            snaps = oi_signals.load_session(inst, session)
            if len(snaps) < 2:
                continue
            sigs = signals(inst, session, snaps)
            for name in CANDIDATES:
                trades += run_session(name, inst, session, snaps, sigs)

    rows = []
    for name, (lane, _, desc) in CANDIDATES.items():
        for inst in instruments:
            mine = [t for t in trades if t["strategy"] == name and t["instrument"] == inst.upper()]
            n, days = len(mine), len({t["session"] for t in mine})
            net = round(sum(t["net"] for t in mine), 2)
            verdict = (
                "COLLECTING"
                if n < MIN_TRADES or days < MIN_DAYS
                else "PASSING"
                if net > 0
                else "DROPPED"
            )
            rows.append(
                {
                    "strategy": name,
                    "lane": lane,
                    "description": desc,
                    "instrument": inst.upper(),
                    "trades": n,
                    "trading_days": days,
                    "win_rate": round(sum(t["net"] > 0 for t in mine) / n, 3) if n else None,
                    "gross": round(sum(t["gross"] for t in mine), 2),
                    "charges": round(sum(t["charges"] for t in mine), 2),
                    "net": net,
                    "per_trade": round(net / n, 2) if n else None,
                    "verdict": verdict,
                }
            )
    return {
        "sessions": len(sessions),
        "bar": {"min_trades": MIN_TRADES, "min_days": MIN_DAYS},
        "rows": rows,
        "recent_trades": sorted(trades, key=lambda t: t["exited"], reverse=True)[:50],
    }
