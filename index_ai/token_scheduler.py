"""Scheduled Dhan token renew / TOTP mint (survives laptop sleep if server stays up)."""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist, parse_time_env

_STATE_PATH = MEMORY_DIR / "dhan_token_schedule.json"


def daily_renew_time() -> time:
    return parse_time_env("DAILY_RENEW_IST", time(8, 0))


def renew_on_startup_enabled() -> bool:
    raw = os.getenv("RENEW_DHAN_TOKEN_ON_STARTUP", "true").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _today_key() -> str:
    return now_ist().date().isoformat()


def startup_renew_due() -> bool:
    """True once per IST day when RENEW_DHAN_TOKEN_ON_STARTUP is enabled."""
    if not renew_on_startup_enabled():
        return False
    state = _load_state()
    return state.get("last_startup_renew_date") != _today_key()


def mark_startup_renew_done() -> None:
    state = _load_state()
    state["last_startup_renew_date"] = _today_key()
    state["last_startup_renew_at"] = now_ist().isoformat(timespec="seconds")
    _save_state(state)


def daily_renew_due(*, window_minutes: int = 20) -> bool:
    """
    True once per IST weekday inside the morning renew window (default 08:00–08:20).
    """
    now = now_ist()
    if now.weekday() >= 5:
        return False
    target = daily_renew_time()
    start = datetime.combine(now.date(), target, tzinfo=now.tzinfo)
    end = start.replace(minute=min(59, target.minute + window_minutes))
    if not (start <= now <= end):
        return False
    state = _load_state()
    return state.get("last_daily_renew_date") != _today_key()


def mark_daily_renew_done() -> None:
    state = _load_state()
    state["last_daily_renew_date"] = _today_key()
    state["last_daily_renew_at"] = now_ist().isoformat(timespec="seconds")
    _save_state(state)


def schedule_status() -> dict[str, Any]:
    state = _load_state()
    return {
        "renew_on_startup": renew_on_startup_enabled(),
        "daily_renew_ist": daily_renew_time().strftime("%H:%M"),
        "last_startup_renew_date": state.get("last_startup_renew_date"),
        "last_daily_renew_date": state.get("last_daily_renew_date"),
        "last_daily_renew_at": state.get("last_daily_renew_at"),
    }
