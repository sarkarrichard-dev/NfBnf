"""The crypto entry gate. One call from ``crypto.lanes``.

Off by default (``CRYPTO_ML_GATE``); even when on it only removes setups once the
model has earned the gate (``gate_armed``). Fails open everywhere else.
"""

from __future__ import annotations

import os
from typing import Any

from crypto.ml import model as crypto_model


def enabled() -> bool:
    return os.getenv("CRYPTO_ML_GATE", "false").strip().lower() in {"1", "true", "yes", "on"}


def check(trade_like: dict[str, Any]) -> dict[str, Any]:
    if not enabled():
        return {"allowed": True, "reason": "crypto ML gate disabled", "win_probability": None}
    v = crypto_model.score(trade_like)
    return {
        "allowed": bool(v["passes"]),
        "reason": v["reason"],
        "win_probability": v["win_probability"],
        "gate": v["gate"],
        "armed": v["armed"],
    }


def status() -> dict[str, Any]:
    return {"enabled": enabled(), **crypto_model.status()}


if __name__ == "__main__":  # self-check
    assert check({})["allowed"] is True  # disabled → open
    os.environ["CRYPTO_ML_GATE"] = "true"
    assert check({"features": {"asset": "BTCUSD"}})["allowed"] is True  # no model → open
    os.environ.pop("CRYPTO_ML_GATE")
    assert status()["enabled"] is False
    print("crypto.ml.gate self-check ok")
