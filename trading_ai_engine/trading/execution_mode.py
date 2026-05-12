from __future__ import annotations

import os
from typing import Any

from trading_ai_engine.dhan.config import load_dhan_config
from trading_ai_engine.openalgo.config import load_openalgo_config
from trading_ai_engine.server import db

MODE_PAPER_LOCAL = "paper_local"
MODE_PAPER_OPENALGO = "paper_openalgo"
MODE_LIVE_DHAN = "live_dhan"

VALID_MODES: frozenset[str] = frozenset({MODE_PAPER_LOCAL, MODE_PAPER_OPENALGO, MODE_LIVE_DHAN})


def get_execution_mode() -> str:
    """
    Order routing mode persisted in SQLite (``operator_prefs``), else ``TRADING_AI_EXECUTION_MODE``,
    else ``paper_openalgo`` when OpenAlgo credentials exist, else ``paper_local``.
    """
    pref = db.get_operator_pref("execution_mode")
    if pref and pref in VALID_MODES:
        return pref
    env = (os.environ.get("TRADING_AI_EXECUTION_MODE") or "").strip().lower()
    if env in VALID_MODES:
        return env
    oa = load_openalgo_config()
    if oa.ready and not _openalgo_disabled():
        return MODE_PAPER_OPENALGO
    return MODE_PAPER_LOCAL


def _openalgo_disabled() -> bool:
    return os.environ.get("TRADING_AI_OPENALGO_DISABLE", "").lower() in ("1", "true", "yes")


def set_execution_mode(mode: str) -> dict[str, Any]:
    m = (mode or "").strip().lower()
    if m not in VALID_MODES:
        return {"ok": False, "reason": "invalid_mode", "allowed": sorted(VALID_MODES)}
    db.set_operator_pref("execution_mode", m)
    return {"ok": True, "execution_mode": m}


def execution_snapshot() -> dict[str, Any]:
    mode = get_execution_mode()
    oa = load_openalgo_config()
    dhan = load_dhan_config()
    return {
        "execution_mode": mode,
        "valid_modes": sorted(VALID_MODES),
        "openalgo": {
            "configured": oa.ready,
            "base_url_display": oa.base_url if oa.ready else None,
            "strategy": oa.strategy,
            "disabled_by_env": _openalgo_disabled(),
        },
        "live_dhan": {
            "credentials_ready": dhan.data_ready,
            "note": (
                "live_dhan is reserved for native Dhan order routing. "
                "When implemented, set DHAN_* keys and enable order sends here."
            ),
        },
        "hints": {
            "paper_openalgo": "Runs placesmartorder on your OpenAlgo instance (use Analyzer/sandbox there).",
            "paper_local": "SQLite-only simulated fills (no broker). AIML learning unchanged.",
            "live_dhan": "Blocked until Dhan order API is wired in this repo; toggle is stored for later.",
        },
    }
