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

Every verdict here is recomputed from ``market_log.chain`` rows on each call --
those rows start 2026-09-23, so there is no Black-Scholes-proxy backtest number
and no pre-cutover Strategy Lab verdict to re-validate; a fresh call against
today's recorded sessions is the only source of truth.

Verdict per (strategy, index), on Richard's readiness bar:
  COLLECTING  fewer than 30 trades or 14 trading days — no call yet
  DROPPED     enough data and net-negative after charges
  PASSING     enough data and net-positive — a candidate to discuss, not to arm
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import sys
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from index_ai import candle_cache, market_log
from index_ai.charges import leg_charge_rupees
from index_ai.config import candle_interval_minutes
from index_ai.instruments import get_instrument, market_lot_size
from index_ai.options_oi import analyze_option_chain
from index_ai.strategies import oi_signals
from index_ai.strategies.buy_strategy import evaluate_buy_signal
from index_ai.strategies.candlestick_sr import intraday_candle_trend
from index_ai.strategies.cpr_regime import analyze_cpr_regime
from index_ai.strategies.strategy import add_indicators
from index_ai.strategies.strategy_params import get_strategy_params

ENTRY_FROM, ENTRY_UNTIL, SQUARE_OFF = "09:30", "14:30", "15:10"
MAX_TRADES_PER_DAY = 2
HEDGE_STRIKES = 4  # long leg sits 4 strikes beyond the short one
# Buys are quick scalps (Richard, 2026-09-24): stop starts this many INDEX
# points from entry and follows 1:1 -- the same rule as the live buy lane
# (instruments._buy_scalp_trail).
BUY_TRAIL_POINTS = {"NIFTY": 25.0, "BANKNIFTY": 55.0, "SENSEX": 80.0}
MIN_TRADES, MIN_DAYS = 30, 14
BUY_MIN_WIN_RATE = 0.65  # Richard's bar for option buying, on top of net > 0
PA_BARS = 6  # today's own 5m candles the structure read looks at (30 min)
# "Options are expensive": at-the-money implied vol (what option prices assume
# the index will move, annualised %) vs the move the index is actually making
# today (5m closes, annualised the same way). Sellers are paid that gap -- the
# best-documented edge in index options. Needs an hour of today's candles.
VRP_MIN_RATIO = 1.2
VRP_MIN_BARS = 12
_BARS_PER_YEAR = 75 * 252  # 5m bars in a 09:15-15:30 session x trading days

# "Switch to next expiry when the current one's premium is thin" (Richard,
# 2026-09-23). Thin = at-the-money premium (avg of CE and PE) below this, in
# rupees per unit -- starting points scaled to each index's size, to be
# replaced by the recorded distribution once there's data. BANKNIFTY's next
# expiry is next month (monthly-only), so expect it to behave differently.
THIN_ATM_PREMIUM = {"NIFTY": 40.0, "BANKNIFTY": 95.0, "SENSEX": 130.0}
NEXT_EXPIRY_SWITCH = {"pa_structure_next_spread"}
_NEXT_MAX_AGE_S = 180  # a next-expiry quote older than this isn't used

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


def _pullback(sig: dict[str, Any]) -> int:
    return sig.get("pullback") or 0


def _wall_bounce(sig: dict[str, Any]) -> int:
    return sig.get("wall_bounce") or 0


def _rich(rule: Direction) -> Direction:
    """Same direction rule, but only when options are expensive vs today's move."""
    return lambda sig: rule(sig) if (sig.get("iv_rv") or 0) >= VRP_MIN_RATIO else 0


# The live option-buy lane's own entry rules (D-01/D-03/D-08/D-09), replayed
# on the recorded chain instead of a new signal reader -- see
# _live_buy_read below. "tuned" just means today's params with these
# overrides applied via dataclasses.replace: no buying against the CPR day
# direction, no buying into a nearby OI wall, and 3 confirmed closes for a
# breakout (up from the live default of 2).
LIVE_BUY_TUNED: dict[str, Any] = {
    "buy_block_contra_cpr": True,
    "buy_block_into_oi_wall": True,
    "entry_confirmation_bars": 3,
}


def _live_buy(sig: dict[str, Any]) -> int:
    return sig["live_buy"](get_strategy_params())


def _live_buy_tuned(sig: dict[str, Any]) -> int:
    return sig["live_buy"](dataclasses.replace(get_strategy_params(), **LIVE_BUY_TUNED))


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
    # The same two directions gated on "options are expensive". Each sits next
    # to its ungated twin, so the lab shows whether the gate itself adds money.
    "vrp_structure_spread": (
        "sell",
        _rich(_structure),
        "pa_structure_spread, only when implied vol is 1.2x+ today's actual move",
    ),
    "vrp_bias_spread": (
        "sell",
        _rich(_bias),
        "oi_bias_spread, only when implied vol is 1.2x+ today's actual move",
    ),
    # Option BUYING without chasing (2026-09-24). 14 of the 15 live buys since
    # 2026-09-10 were entered after the index had already run the trade's way
    # for 30 min (-₹5,981); direction 15 min later was right 8/15. These
    # enter on the pause inside a move instead. Judged on win rate too --
    # Richard's bar for buying is 65%.
    "pa_pullback_buy": (
        "buy",
        _pullback,
        "Buy ATM option when today's 5m structure resumes after a one-candle dip",
    ),
    "oi_wall_bounce_buy": (
        "buy",
        _wall_bounce,
        "Buy ATM call when the index taps the biggest put-OI strike and closes back above (puts mirrored)",
    ),
    "pa_structure_next_spread": (
        "sell",
        _structure,
        "pa_structure_spread, but sold on next expiry when this expiry's ATM premium is thin",
    ),
    # D-08/D-09: the live buy lane's own evaluate_buy_signal, replayed on the
    # real recorded chain -- not a new signal, a sanity check of the existing
    # one. On-demand only (see ON_DEMAND below): /api/strategy-lab's default
    # run() never evaluates these, so the dashboard's per-call cost is
    # unchanged.
    "live_buy_lane": (
        "buy",
        _live_buy,
        "The live option-buy lane's own entry rules at today's settings, replayed on the recorded chain -- buys the ATM option",
    ),
    "live_buy_lane_tuned": (
        "buy",
        _live_buy_tuned,
        "live_buy_lane, but no buying against the CPR day direction or on a sideways CPR day, "
        "no buying into a nearby OI wall, and 3 confirmed closes for a breakout",
    ),
}

# Evaluated only when explicitly requested via run(..., names=...) -- not by
# the dashboard's GET /api/strategy-lab, which always calls run() with no
# names and only ever exercises the candidates below.
ON_DEMAND = frozenset({"live_buy_lane", "live_buy_lane_tuned"})


@dataclass
class _Pos:
    legs: list[tuple[float, str, str, float]]  # (strike, CE/PE, BUY/SELL, entry price)
    entered: str
    direction: int
    basis: float  # credit received / premium paid, points
    charges: float = 0.0
    next_expiry: bool = False
    anchor: float = 0.0  # buys: best index price since entry
    seen_to: str = ""  # buys: index path already walked up to here


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


def _trail_hit(pos: _Pos, path: pd.Series, until: str, dist: float, spot: float) -> bool:
    """Walk the real index ticks since the last check (or just this snapshot's
    spot when no ticks were recorded); move the anchor with each new best
    price and report whether the 1:1 trailing stop was touched."""
    if path.empty:
        prices = [spot]
    else:
        seg = path[(path.index > pd.Timestamp(pos.seen_to)) & (path.index <= pd.Timestamp(until))]
        prices = list(seg.to_numpy()) + [spot]
    pos.seen_to = until
    for px in prices:
        if pos.direction > 0:
            pos.anchor = max(pos.anchor, px)
            if px <= pos.anchor - dist:
                return True
        else:
            pos.anchor = min(pos.anchor, px)
            if px >= pos.anchor + dist:
                return True
    return False


def _index_path(instrument: str, session: str) -> pd.Series:
    """The index's real recorded prices for the session, indexed by the
    EXCHANGE's own trade time (``ltt``), not when the tick reached us --
    delivery usually lags 3-10s but spiked to 7 min on 2026-09-16 and 29 min
    on 2026-09-17. Dhan's ltt is IST wall-clock seconds stored as an epoch,
    so decode it as UTC. 09:15-15:30 only."""
    with market_log.connect() as db:
        rows = db.execute(
            "SELECT ltt, ltp FROM ticks WHERE session=? AND instrument=? AND ltp > 0 AND ltt > 0",
            (session, instrument.upper()),
        ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows], unit="s").tz_localize("Asia/Kolkata")
    px = pd.Series([float(r[1]) for r in rows], index=idx).sort_index()
    day = pd.Timestamp(session, tz="Asia/Kolkata")
    return px[
        (px.index >= day + pd.Timedelta(hours=9, minutes=15))
        & (px.index < day + pd.Timedelta(hours=15, minutes=30))
    ]


def _bars_5m(instrument: str, session: str, minutes: int = 5) -> pd.DataFrame:
    """Today's ``minutes``-wide OHLC for the index, from _index_path (exchange
    time). Default 5m for the existing candidates; the live buy lane replay
    passes the active candle interval (usually 1m) instead."""
    px = _index_path(instrument, session)
    if px.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "end"])
    bars = (
        px.resample(f"{minutes}min", origin="start_day", offset="15min")
        .agg(["first", "max", "min", "last"])
        .dropna()
    )
    bars.columns = ["open", "high", "low", "close"]
    bars["end"] = bars.index + pd.Timedelta(minutes=minutes)
    return bars


def _prev_day(instrument: str, session: str) -> pd.DataFrame:
    """The prior session's cached intraday candles (for previous_day_cpr) --
    an empty frame, never a raise, when nothing is cached that far back."""
    day = date.fromisoformat(session)
    cached = candle_cache.load_cached_range(
        instrument,
        candle_interval_minutes(),
        from_date=day - timedelta(days=10),
        to_date=day - timedelta(days=1),
    )
    if cached.empty:
        return cached
    latest = pd.to_datetime(cached["datetime"]).dt.date.max()
    return cached[pd.to_datetime(cached["datetime"]).dt.date == latest].reset_index(drop=True)


def _pullback_read(structure: str, done: pd.DataFrame) -> int:
    """+1 / -1 when the last completed 5m candle resumes today's structure
    after a one-candle dip: structure UP, the previous candle closed down, and
    the last one closed green above that dip candle's high (puts mirrored)."""
    if structure not in ("UP", "DOWN") or len(done) < 2:
        return 0
    prev, last = done.iloc[-2], done.iloc[-1]
    if (
        structure == "UP"
        and prev["close"] < prev["open"]
        and last["close"] > last["open"]
        and last["close"] > prev["high"]
    ):
        return 1
    if (
        structure == "DOWN"
        and prev["close"] > prev["open"]
        and last["close"] < last["open"]
        and last["close"] < prev["low"]
    ):
        return -1
    return 0


def _wall_bounce_read(snap: oi_signals.Snapshot, done: pd.DataFrame, sig: dict[str, Any]) -> int:
    """+1 when the last completed 5m candle dipped to the max put-OI strike
    (support) and closed back above it; -1 mirrored at the max call-OI
    strike (resistance). Not against a clear opposite structure."""
    if not len(done):
        return 0
    side = oi_signals._oi_by_side(snap)
    sup, res = oi_signals._wall(side["PE"]), oi_signals._wall(side["CE"])
    last = done.iloc[-1]
    if sup and last["low"] <= sup < last["close"] and sig.get("structure") != "DOWN":
        return 1
    if res and last["high"] >= res > last["close"] and sig.get("structure") != "UP":
        return -1
    return 0


def _iv_rv(snap: oi_signals.Snapshot, done: pd.DataFrame) -> float | None:
    """ATM implied vol / today's realised vol, both annualised %. None until
    there's an hour of candles and a real IV at the strike nearest spot."""
    if len(done) < VRP_MIN_BARS + 1:
        return None
    spot = float(snap[0]["spot"] or 0)
    atm = min({r["strike"] for r in snap}, key=lambda k: abs(k - spot))
    ivs = [float(r["iv"]) for r in snap if r["strike"] == atm and r.get("iv")]
    rets = np.log(done["close"].astype(float)).diff().dropna()
    rv = float(rets.std()) * math.sqrt(_BARS_PER_YEAR) * 100
    if not ivs or not rv:
        return None
    return round(sum(ivs) / len(ivs) / rv, 3)


def _live_buy_read(
    instrument: str,
    session: str,
    ts: str,
    snap: oi_signals.Snapshot,
    ctx: dict[str, Any],
    params: Any,
) -> int:
    """Replay the live buy lane's own evaluate_buy_signal at this snapshot --
    same frame/regime construction order strategy_router.evaluate_dual_
    opportunities uses. ``ctx`` caches the per-session bars/prev-day lookup
    across every snapshot's call (set once, on the first call). Returns 0
    (no trade) on missing prior-day data, too few completed candles, or the
    ValueError evaluate_buy_signal raises early in the session -- never
    raises itself."""
    if "bars" not in ctx:
        ctx["bars"] = _bars_5m(instrument, session, minutes=int(candle_interval_minutes()))
        ctx["prev"] = _prev_day(instrument, session)
    bars, prev = ctx["bars"], ctx["prev"]
    if prev.empty or not len(bars):
        return 0
    # ponytail: the live scanner also sees the still-forming bar; the lab
    # deliberately only uses bars that have fully closed by this snapshot
    # (no look-ahead).
    done = bars[bars["end"] <= pd.Timestamp(ts)]
    if done.empty:
        return 0
    frame = add_indicators(
        done.reset_index(drop=True), fast=params.ema_fast_period, slow=params.ema_slow_period
    )
    row = frame.iloc[-1]
    regime = analyze_cpr_regime(
        frame,
        prev,
        price=float(row["close"]),
        ema_fast=float(row["ema_fast"]),
        ema_slow=float(row["ema_slow"]),
    )
    rows: dict[str, dict[str, Any]] = {}
    for r in snap:
        rows.setdefault(str(r["strike"]), {})[r["opt_type"].lower()] = {"oi": r.get("oi") or 0}
    chain = {"data": {"oc": rows}}
    oi = analyze_option_chain(
        chain, spot=float(snap[0]["spot"] or 0), instrument=get_instrument(instrument)
    )
    try:
        sig = evaluate_buy_signal(frame, prev, regime, params=params, oi=oi)
    except ValueError:
        return 0
    return {"BUY_CALL": 1, "BUY_PUT": -1}.get(sig.action, 0)


def signals(
    instrument: str, session: str, snaps: list[tuple[str, oi_signals.Snapshot]]
) -> list[dict[str, Any] | None]:
    """OI read per snapshot, plus ``structure``: UP/DOWN/RANGE from today's
    completed 5m candles up to that moment (no peeking at the bar in progress).
    ``live_buy`` is a lazy callable (one evaluate_buy_signal call costs about
    15 ms) -- only run_session's on-demand live_buy_lane* candidates call it."""
    bars = _bars_5m(instrument, session)
    ctx: dict[str, Any] = {}
    out: list[dict[str, Any] | None] = [None]
    for ts, snap in snaps[1:]:
        sig = oi_signals.read(snaps[0][1], snap, session)
        done = bars[bars["end"] <= pd.Timestamp(ts)] if len(bars) else bars
        sig["structure"] = intraday_candle_trend(done, lookback=PA_BARS)
        sig["iv_rv"] = _iv_rv(snap, done)
        sig["pullback"] = _pullback_read(sig["structure"], done)
        sig["wall_bounce"] = _wall_bounce_read(snap, done, sig)
        sig["live_buy"] = functools.partial(_live_buy_read, instrument, session, ts, snap, ctx)
        out.append(sig)
    return out


def _thin(instrument: str, snap: oi_signals.Snapshot) -> bool:
    spot = float(snap[0]["spot"] or 0)
    atm = min({r["strike"] for r in snap}, key=lambda k: abs(k - spot))
    prem = [float(r["ltp"]) for r in snap if r["strike"] == atm and r.get("ltp")]
    return bool(prem) and sum(prem) / len(prem) < THIN_ATM_PREMIUM.get(instrument.upper(), 0)


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

    nxt = oi_signals.load_session(instrument, session, rank=1) if name in NEXT_EXPIRY_SWITCH else []
    nxt_ts = [t for t, _ in nxt]

    def _next_at(ts: str) -> oi_signals.Snapshot | None:
        j = bisect_right(nxt_ts, ts) - 1
        if j < 0 or (pd.Timestamp(ts) - pd.Timestamp(nxt_ts[j])).total_seconds() > _NEXT_MAX_AGE_S:
            return None
        return nxt[j][1]

    path = _index_path(instrument, session)
    from index_ai.strategies.credit_spread import SELL_TRAIL_POINTS

    trail_dist = (BUY_TRAIL_POINTS if lane == "buy" else SELL_TRAIL_POINTS).get(
        instrument.upper(), 25.0 if lane == "buy" else 40.0
    )
    trades: list[dict[str, Any]] = []
    pos: _Pos | None = None
    for i in range(1, len(snaps)):
        ts, snap = snaps[i]
        hhmm = ts[11:16]
        q = _quotes(snap)
        closing = hhmm >= SQUARE_OFF
        if pos and pos.next_expiry:
            q = _quotes(_next_at(ts) or [])
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
            if closing:
                why = "square-off"
            else:
                # Richard's 1:1 index trail, both lanes (live uses the same
                # numbers). Exits at this snapshot's real quotes once the index
                # touched the stop since the last one -- snapshots are ~90s
                # apart and live checks every 20s, so a little pessimistic.
                why = (
                    "trail stop"
                    if _trail_hit(pos, path, ts, trail_dist, float(snap[0]["spot"] or 0))
                    else None
                )
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
                        "expiry": "next" if pos.next_expiry else "near",
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
        use = snap
        if name in NEXT_EXPIRY_SWITCH and _thin(instrument, snap):
            use = _next_at(ts)
            if use is None:
                continue  # thin here and no fresh next-expiry quote: no trade
            q = _quotes(use)
        legs = _open(lane, direction, use)
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
            next_expiry=use is not snap,
            anchor=float(use[0]["spot"] or 0),
            seen_to=ts,
        )
    # ponytail: a position still open when the day's recording stops (mid-session
    # view, or the server went down before 15:10) is not counted -- no fake exit.
    return trades


def run(
    instruments: list[str], sessions: list[str], names: list[str] | None = None
) -> dict[str, Any]:
    """Every candidate x index x recorded day, plus a verdict per pair.

    ``names`` defaults to every registered candidate EXCEPT ``ON_DEMAND``
    (the live_buy_lane* candidates) -- this is what GET /api/strategy-lab
    calls with no names, so its per-request cost is unchanged. Pass
    ``names=sorted(ON_DEMAND)`` (or include them explicitly) to evaluate
    those too, e.g. from the CLI below.
    """
    names = list(names) if names else [n for n in CANDIDATES if n not in ON_DEMAND]
    trades: list[dict[str, Any]] = []
    for inst in instruments:
        for session in sessions:
            snaps = oi_signals.load_session(inst, session)
            if len(snaps) < 2:
                continue
            sigs = signals(inst, session, snaps)
            for name in names:
                trades += run_session(name, inst, session, snaps, sigs)

    rows = []
    for name in names:
        lane, _, desc = CANDIDATES[name]
        for inst in instruments:
            mine = [t for t in trades if t["strategy"] == name and t["instrument"] == inst.upper()]
            n, days = len(mine), len({t["session"] for t in mine})
            net = round(sum(t["net"] for t in mine), 2)
            win = sum(t["net"] > 0 for t in mine) / n if n else 0.0
            if n < MIN_TRADES or days < MIN_DAYS:
                verdict = "COLLECTING"
            elif net > 0 and (lane != "buy" or win >= BUY_MIN_WIN_RATE):
                verdict = "PASSING"
            else:
                verdict = "DROPPED"
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


if __name__ == "__main__":
    _instruments = [a.upper() for a in sys.argv[1:]] or ["NIFTY", "BANKNIFTY", "SENSEX"]
    with market_log.connect() as _db:
        _sessions = [r[0] for r in _db.execute("SELECT DISTINCT session FROM chain ORDER BY 1")]
    _result = run(_instruments, _sessions, names=sorted(ON_DEMAND))
    print(
        json.dumps(
            {
                "sessions": _sessions,
                "baseline": {k: getattr(get_strategy_params(), k) for k in LIVE_BUY_TUNED},
                "tuned": LIVE_BUY_TUNED,
                "rows": _result["rows"],
            },
            indent=1,
            default=str,
        )
    )
