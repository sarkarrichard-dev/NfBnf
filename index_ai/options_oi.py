from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from index_ai.instruments import IndexInstrument
from index_ai.strategies.strategy import StrategySignal, copy_signal, nearest_strike


@dataclass(frozen=True)
class OptionOiContext:
    spot: float
    atm_strike: float
    total_call_oi: int
    total_put_oi: int
    pcr: float
    max_call_oi_strike: float | None
    max_put_oi_strike: float | None
    bias: str
    note: str
    confidence_adjustment: float
    max_pain: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def oi_walls(oi: "OptionOiContext | None") -> tuple[float, float] | None:
    """``(support, resistance)`` from the max-put/max-call OI strikes, or
    ``None`` if the chain is missing, flat, or the walls are crossed —
    shared by every lane that wants "the real S/R", not a candle-range guess
    (the sell lane's `oi_credit.decide`, the buy lane's candlestick S/R)."""
    if oi is None or oi.max_put_oi_strike is None or oi.max_call_oi_strike is None:
        return None
    support, resistance = float(oi.max_put_oi_strike), float(oi.max_call_oi_strike)
    if support >= resistance:
        return None
    return support, resistance


def _leg_oi(leg: dict[str, Any]) -> int:
    for key in ("oi", "open_interest", "OI"):
        val = leg.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 0


def _leg_volume(leg: dict[str, Any]) -> int:
    val = leg.get("volume")
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    return 0


def _leg_greek(leg: dict[str, Any], name: str) -> float | None:
    val = (leg.get("greeks") or {}).get(name)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _theta_drag_ratio(leg: dict[str, Any]) -> float:
    """Daily time-decay as a fraction of the premium paid — lower bleeds less
    per rupee. Missing theta or a worthless premium can't be compared, so it
    sorts last (never wins a tiebreak over a leg we can actually price)."""
    theta = _leg_greek(leg, "theta")
    ltp = leg.get("last_price")
    try:
        premium = float(ltp) if ltp is not None else 0.0
    except (TypeError, ValueError):
        premium = 0.0
    if theta is None or premium <= 0:
        return float("inf")
    return abs(theta) / premium


def analyze_option_chain(
    chain: dict[str, Any],
    *,
    spot: float,
    instrument: IndexInstrument,
    strike_window: int = 21,  # ~10 strikes each side of ATM — BANKNIFTY's real walls
    # (100-pt strikes, monthly expiry) sit further out than a ±5 window catches
) -> OptionOiContext:
    """Summarize OI around ATM from Dhan option chain."""
    rows = (chain.get("data") or {}).get("oc") or {}
    if not rows:
        return OptionOiContext(
            spot=spot,
            atm_strike=nearest_strike(spot, instrument),
            total_call_oi=0,
            total_put_oi=0,
            pcr=1.0,
            max_call_oi_strike=None,
            max_put_oi_strike=None,
            bias="unknown",
            note="No option chain rows.",
            confidence_adjustment=0.0,
        )

    parsed: list[tuple[float, dict[str, Any]]] = []
    for key, row in rows.items():
        try:
            parsed.append((float(key), row))
        except ValueError:
            continue
    parsed.sort(key=lambda x: abs(x[0] - spot))
    window = parsed[: max(1, strike_window)]

    total_call_oi = 0
    total_put_oi = 0
    best_call: tuple[float, int] = (0.0, 0)
    best_put: tuple[float, int] = (0.0, 0)

    for strike, row in window:
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        coi = _leg_oi(ce)
        poi = _leg_oi(pe)
        total_call_oi += coi
        total_put_oi += poi
        if coi > best_call[1]:
            best_call = (strike, coi)
        if poi > best_put[1]:
            best_put = (strike, poi)

    pcr = round(total_put_oi / max(total_call_oi, 1), 3)
    atm = nearest_strike(spot, instrument)

    mp: float | None = None
    try:
        from index_ai.market_context.oi_flow import max_pain as _max_pain

        mp = _max_pain({strike: row for strike, row in parsed})
    except Exception:
        mp = None

    if pcr > 1.15:
        bias = "put_heavy"
        note = f"Put OI dominant near ATM (PCR {pcr:.2f})."
    elif pcr < 0.85:
        bias = "call_heavy"
        note = f"Call OI dominant near ATM (PCR {pcr:.2f})."
    else:
        bias = "balanced"
        note = f"Balanced OI near ATM (PCR {pcr:.2f})."

    return OptionOiContext(
        spot=spot,
        atm_strike=float(atm),
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        pcr=pcr,
        max_call_oi_strike=best_call[0] if best_call[1] else None,
        max_put_oi_strike=best_put[0] if best_put[1] else None,
        bias=bias,
        note=note,
        confidence_adjustment=0.0,
        max_pain=mp,
    )


def oi_confidence_adjustment(action: str, oi: OptionOiContext) -> float:
    """Boost or cut confidence when OI aligns or fights the CPR+EMA direction."""
    if action == "NO_TRADE":
        return 0.0
    if action == "BUY_CALL":
        if oi.bias == "call_heavy":
            return 0.06
        if oi.bias == "put_heavy":
            return -0.08
        return 0.02
    if action == "BUY_PUT":
        if oi.bias == "put_heavy":
            return 0.06
        if oi.bias == "call_heavy":
            return -0.08
        return 0.02
    return 0.0


def apply_oi_to_signal(signal: StrategySignal, oi: OptionOiContext) -> StrategySignal:
    if signal.action == "NO_TRADE":
        return signal
    adj = oi_confidence_adjustment(signal.action, oi)
    conf = max(0.0, min(1.0, signal.confidence + adj))
    reason = f"{signal.reason} {oi.note}"
    if adj < 0:
        reason += " OI does not confirm direction."
    elif adj > 0:
        reason += " OI supports direction."
    return copy_signal(
        signal,
        reason=reason,
        confidence=round(conf, 3),
    )


def _pick_by_liquidity(
    candidates: list[tuple[float, dict[str, Any]]],
) -> tuple[float, dict[str, Any]]:
    def score(leg: dict[str, Any]) -> float:
        return _leg_oi(leg) + _leg_volume(leg) * 0.1

    return max(candidates, key=lambda item: score(item[1]))


def _pick_by_greeks(
    candidates: list[tuple[float, dict[str, Any]]],
    *,
    delta_low: float,
    delta_high: float,
    min_oi: int,
    min_volume: int,
) -> tuple[float, dict[str, Any]] | None:
    """Prefer the strike whose |delta| sits in the target band — real
    sensitivity to the underlying, not a cheap low-probability lottery ticket
    or an expensive near-certainty with little leverage left. Gamma peaks in
    this same near-the-money band, so targeting delta already leans toward
    the strikes that accelerate fastest once the trade is working — a
    separate gamma term would mostly double-count it. Ties within the band
    go to whichever leg bleeds the least time-value per rupee paid (theta),
    then to the more liquid one.

    Ranking by delta instead of liquidity means this can legitimately prefer
    a much thinner strike than the old picker ever would have — so
    ``min_oi``/``min_volume`` drop genuinely illiquid candidates before
    ranking starts. If *every* candidate is that thin, the floor is dropped
    rather than blocking the trade (an unusually quiet day shouldn't stop
    the lane outright — it just means delta ranking runs on what's there).

    Returns ``None`` (fall back to liquidity-only) when no candidate has a
    delta at all — a live-data gap must never stop the buy lane.
    """
    scored = [(strike, leg, _leg_greek(leg, "delta")) for strike, leg in candidates]
    if all(d is None for _, _, d in scored):
        return None

    liquid = [
        item for item in scored if _leg_oi(item[1]) >= min_oi and _leg_volume(item[1]) >= min_volume
    ]
    if liquid:
        scored = liquid

    def band_distance(d: float | None) -> float:
        if d is None:
            return float("inf")
        ad = abs(d)
        if ad < delta_low:
            return delta_low - ad
        if ad > delta_high:
            return ad - delta_high
        return 0.0

    def key(item: tuple[float, dict[str, Any], float | None]) -> tuple[float, float, float]:
        _, leg, d = item
        return (band_distance(d), _theta_drag_ratio(leg), -_leg_oi(leg) - _leg_volume(leg) * 0.1)

    strike, leg, _ = min(scored, key=key)
    return strike, leg


def choose_option_from_chain_with_oi(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    oi: OptionOiContext,
    *,
    transaction_type: str = "BUY",
) -> dict[str, Any]:
    """Pick a strike near ATM from the Dhan chain — by delta/theta (the real
    Greeks Dhan already sends per leg) when the config wants that, else the
    older pure OI + volume liquidity pick."""
    from index_ai.strategies.strategy_params import get_strategy_params

    side = "ce" if signal.action == "BUY_CALL" else "pe"
    rows = (chain.get("data") or {}).get("oc") or {}
    if not rows:
        raise RuntimeError("Dhan option chain returned no strikes.")

    target = nearest_strike(signal.price, instrument)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for key, row in rows.items():
        try:
            strike = float(key)
        except ValueError:
            continue
        if abs(strike - target) > instrument.strike_step * 2:
            continue
        leg = row.get(side) or {}
        if leg.get("security_id") is None:
            continue
        candidates.append((strike, leg))

    if not candidates:
        raise RuntimeError("No option legs with security_id near ATM.")

    cfg = get_strategy_params()
    picked = None
    if cfg.buy_use_greeks_strike_selection:
        picked = _pick_by_greeks(
            candidates,
            delta_low=cfg.buy_target_delta_low,
            delta_high=cfg.buy_target_delta_high,
            min_oi=cfg.buy_greeks_min_oi,
            min_volume=cfg.buy_greeks_min_volume,
        )
    strike, leg = picked or _pick_by_liquidity(candidates)

    oi_adj = oi_confidence_adjustment(signal.action, oi)
    max_pain = oi.max_pain
    return {
        "instrument": instrument.key,
        "option_type": "CALL" if side == "ce" else "PUT",
        "strike": strike,
        "security_id": int(leg["security_id"]),
        "segment": instrument.option_segment,
        "ltp": leg.get("last_price"),
        "quantity": instrument.lot_size,
        "transaction_type": transaction_type.upper(),
        "oi": _leg_oi(leg),
        "volume": _leg_volume(leg),
        "delta": _leg_greek(leg, "delta"),
        "theta": _leg_greek(leg, "theta"),
        "gamma": _leg_greek(leg, "gamma"),
        "chain_pcr": oi.pcr,
        "chain_bias": oi.bias,
        "oi_confidence_adjustment": oi_adj,
        "oi_note": oi.note,
        "total_call_oi": oi.total_call_oi,
        "total_put_oi": oi.total_put_oi,
        # not used to pick the strike yet — tracked so we can measure whether
        # trades near max pain actually fare worse before ever gating on it
        "max_pain": max_pain,
        "distance_to_max_pain": (abs(strike - max_pain) if max_pain else None),
    }
