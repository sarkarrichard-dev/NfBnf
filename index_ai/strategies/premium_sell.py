"""Naked ATM premium selling (Apex Pivot-Trend style) — single-leg short options."""

from __future__ import annotations

PREMIUM_SELL_ACTIONS = frozenset(
    {
        "SELL_ATM_PUT",
        "SELL_ATM_CALL",
    }
)


def is_premium_sell_action(action: str) -> bool:
    return str(action or "").upper() in PREMIUM_SELL_ACTIONS


def premium_sell_entry_ready(option: dict[str, Any], *, action: str = "") -> tuple[bool, str]:
    act = str(action or "").upper()
    if not is_premium_sell_action(act):
        return True, "not premium sell"
    if option.get("security_id") is None:
        return False, "ATM option missing security_id."
    if not str(option.get("option_type") or "").upper():
        return False, "ATM option missing CALL/PUT type."
    if str(option.get("transaction_type") or "SELL").upper() != "SELL":
        return False, "Premium sell must be SELL transaction."
    qty = int(option.get("quantity") or 0)
    if qty <= 0:
        return False, "ATM option missing quantity."
    expected_side = "PUT" if act == "SELL_ATM_PUT" else "CALL"
    if str(option.get("option_type") or "").upper() != expected_side:
        return False, f"Action {act} requires {expected_side}, got {option.get('option_type')}."
    return True, "ok"
