from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from index_ai.instruments import IndexInstrument


@dataclass(frozen=True)
class StrategySignal:
    action: str
    reason: str
    confidence: float
    price: float
    pivot: float
    bc: float
    tc: float
    ema_fast: float
    ema_slow: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def add_indicators(candles: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    df = candles.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    return df


def previous_day_cpr(previous_day: pd.DataFrame) -> tuple[float, float, float]:
    high = float(previous_day["high"].max())
    low = float(previous_day["low"].min())
    close = float(previous_day["close"].iloc[-1])
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = 2.0 * pivot - bc
    return pivot, min(bc, tc), max(bc, tc)


def cpr_ema_signal(today: pd.DataFrame, previous_day: pd.DataFrame) -> StrategySignal:
    if len(today) < 21:
        raise ValueError("Need at least 21 intraday candles for EMA signal.")
    df = add_indicators(today)
    row = df.iloc[-1]
    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    pivot, bc, tc = previous_day_cpr(previous_day)

    if price > tc and ema_fast > ema_slow:
        distance = min(1.0, abs(price - tc) / max(price * 0.004, 1.0))
        confidence = 0.55 + distance * 0.25
        return StrategySignal(
            action="BUY_CALL",
            reason="Price is above CPR top and EMA fast is above EMA slow.",
            confidence=round(confidence, 3),
            price=price,
            pivot=pivot,
            bc=bc,
            tc=tc,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
        )
    if price < bc and ema_fast < ema_slow:
        distance = min(1.0, abs(price - bc) / max(price * 0.004, 1.0))
        confidence = 0.55 + distance * 0.25
        return StrategySignal(
            action="BUY_PUT",
            reason="Price is below CPR bottom and EMA fast is below EMA slow.",
            confidence=round(confidence, 3),
            price=price,
            pivot=pivot,
            bc=bc,
            tc=tc,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
        )
    return StrategySignal(
        action="NO_TRADE",
        reason="CPR and EMA are not aligned.",
        confidence=0.0,
        price=price,
        pivot=pivot,
        bc=bc,
        tc=tc,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )


def nearest_strike(price: float, instrument: IndexInstrument) -> int:
    step = instrument.strike_step
    return int(round(price / step) * step)


def choose_option_from_chain(
    chain: dict[str, Any],
    signal: StrategySignal,
    instrument: IndexInstrument,
    *,
    transaction_type: str = "BUY",
) -> dict[str, Any]:
    strike = nearest_strike(signal.price, instrument)
    side = "ce" if signal.action == "BUY_CALL" else "pe"
    option_rows = (chain.get("data") or {}).get("oc") or {}
    if not option_rows:
        raise RuntimeError("Dhan option chain returned no strikes.")
    selected_key = min(option_rows.keys(), key=lambda key: abs(float(key) - strike))
    selected = float(selected_key)
    row = option_rows.get(selected_key) or {}
    leg = row.get(side) or {}
    security_id = leg.get("security_id")
    if security_id is None:
        raise RuntimeError("Selected strike has no option security id.")
    return {
        "instrument": instrument.key,
        "option_type": "CALL" if side == "ce" else "PUT",
        "strike": selected,
        "security_id": int(security_id),
        "segment": instrument.option_segment,
        "ltp": leg.get("last_price"),
        "quantity": instrument.lot_size,
        "transaction_type": transaction_type.upper(),
    }
