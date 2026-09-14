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


def choose_option_from_chain_with_oi(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    oi: OptionOiContext,
    *,
    transaction_type: str = "BUY",
) -> dict[str, Any]:
    """Pick liquid ATM-ish strike using OI + volume from Dhan chain."""
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

    def score(leg: dict[str, Any]) -> float:
        return _leg_oi(leg) + _leg_volume(leg) * 0.1

    strike, leg = max(candidates, key=lambda item: score(item[1]))
    oi_adj = oi_confidence_adjustment(signal.action, oi)
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
        "chain_pcr": oi.pcr,
        "chain_bias": oi.bias,
        "oi_confidence_adjustment": oi_adj,
        "oi_note": oi.note,
        "total_call_oi": oi.total_call_oi,
        "total_put_oi": oi.total_put_oi,
    }
