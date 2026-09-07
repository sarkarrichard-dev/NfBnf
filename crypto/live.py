"""Crypto live-trading locks — the mode switch and the arming phrase.

Mirrors ``index_ai.config``'s ``set_trading_mode`` / ``arm_live_trading`` /
``disarm_live_trading``, but with its **own** env keys and its **own** phrase so
arming one venue never arms the other. Two independent locks plus valid Delta
credentials are all required (``CryptoSettings.live_orders_enabled``):

    CRYPTO_TRADING_MODE=LIVE   AND   CRYPTO_ALLOW_LIVE=true   AND   keys set

Switching to PAPER always disarms — the safe direction never needs ceremony.
"""

from __future__ import annotations

from index_ai.config import update_env_values

CRYPTO_ARM_PHRASE = "ARM CRYPTO LIVE"


def set_crypto_mode(mode: str) -> str:
    """PAPER (journal only) or LIVE (real Delta orders). LIVE alone places no
    orders — arming is a separate, confirmed step."""
    normalized = str(mode or "").strip().upper()
    if normalized not in {"PAPER", "LIVE"}:
        raise ValueError("mode must be PAPER or LIVE")
    values = {"CRYPTO_TRADING_MODE": normalized}
    if normalized == "PAPER":
        values["CRYPTO_ALLOW_LIVE"] = "false"
    update_env_values(values)
    return normalized


def arm_crypto_live(confirm: str) -> bool:
    """Allow real Delta orders. Requires the exact confirmation phrase."""
    if str(confirm or "").strip().upper() != CRYPTO_ARM_PHRASE:
        raise ValueError(f'Confirmation required: type "{CRYPTO_ARM_PHRASE}" exactly.')
    update_env_values({"CRYPTO_ALLOW_LIVE": "true"})
    return True


def disarm_crypto_live() -> bool:
    """Block real Delta orders. No confirmation — safety is always one click."""
    update_env_values({"CRYPTO_ALLOW_LIVE": "false"})
    return True


if __name__ == "__main__":  # self-check — no .env write (update_env_values stubbed)
    writes: dict[str, str] = {}
    update_env_values = lambda v: writes.update(v)  # noqa: E731 — stub for the self-check

    assert set_crypto_mode("live") == "LIVE" and writes == {"CRYPTO_TRADING_MODE": "LIVE"}
    writes.clear()
    assert set_crypto_mode("paper") == "PAPER"
    assert writes["CRYPTO_ALLOW_LIVE"] == "false"  # PAPER disarms
    writes.clear()
    try:
        arm_crypto_live("arm live orders")  # the index phrase must NOT work here
        raise AssertionError("wrong phrase accepted")
    except ValueError:
        pass
    assert arm_crypto_live("ARM CRYPTO LIVE") and writes == {"CRYPTO_ALLOW_LIVE": "true"}
    writes.clear()
    assert disarm_crypto_live() and writes == {"CRYPTO_ALLOW_LIVE": "false"}
    assert CRYPTO_ARM_PHRASE != "ARM LIVE ORDERS"
    print("crypto.live self-check ok")
