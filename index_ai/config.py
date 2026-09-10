from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from index_ai.risk_policy import HARDCODED_RISK

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
MEMORY_DIR = ROOT / "memory"
DB_PATH = MEMORY_DIR / "trade_memory.sqlite"
DASHBOARD_DIR = ROOT / "dashboard" / "dist"


@dataclass(frozen=True)
class DhanSettings:
    client_id: str
    access_token: str
    api_base_url: str
    api_key: str
    api_secret: str
    auth_base_url: str
    token_expiry: str

    @property
    def ready(self) -> bool:
        return bool(self.client_id and self.access_token)

    @property
    def app_credentials_ready(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def can_generate_consent(self) -> bool:
        return bool(self.client_id and self.api_key and self.api_secret)


@dataclass(frozen=True)
class RiskSettings:
    trading_mode: str
    allow_live_trading: bool
    allow_option_buying: bool
    allow_option_selling: bool
    max_losing_trades_per_day: int
    max_daily_loss_rupees: float
    trailing_stop_index_points: float
    min_confidence: float
    max_profit_cap_rupees: float | None


@dataclass(frozen=True)
class AppSettings:
    dhan: DhanSettings
    risk: RiskSettings


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


_ENV_FROZEN = False


def freeze_env() -> None:
    """Stop _load_env() from reloading .env, pinning the current os.environ.

    ``_load_env()`` runs ``load_dotenv(override=True)``, which silently reverts any
    env var set programmatically (e.g. STRATEGY_STYLE / CANDLE_INTERVAL_MINUTES in
    the research backtest runners) back to the .env value. Scripts that drive
    those vars call this once, after reading ``settings()``.
    """
    global _ENV_FROZEN
    _ENV_FROZEN = True


def _load_env() -> None:
    if _ENV_FROZEN or os.getenv("PYTEST_CURRENT_TEST"):
        return
    load_dotenv(ENV_PATH, override=True)


VALID_CANDLE_INTERVALS = frozenset({"1", "5", "15", "25", "60"})


def candle_interval_minutes() -> str:
    """Dhan intraday chart interval (minutes). Default 1m spot chart for EMA/CPR/strategies."""
    _load_env()
    raw = os.getenv("CANDLE_INTERVAL_MINUTES", "1").strip()
    return raw if raw in VALID_CANDLE_INTERVALS else "1"


def candle_interval_int() -> int:
    return max(1, int(candle_interval_minutes()))


def bars_for_minutes(minutes: int) -> int:
    """Convert a time window to bar count at the active chart interval."""
    return max(1, round(float(minutes) / candle_interval_int()))


def settings() -> AppSettings:
    repair_env_access_token_line()
    _load_env()
    try:
        from index_ai.dhan_auth import reconcile_env_with_jwt

        reconcile_env_with_jwt()
        _load_env()
    except Exception:
        pass
    return AppSettings(
        dhan=DhanSettings(
            client_id=os.getenv("DHAN_CLIENT_ID", "").strip(),
            access_token=os.getenv("DHAN_ACCESS_TOKEN", "").strip(),
            api_base_url=os.getenv("DHAN_API_BASE_URL", "https://api.dhan.co/v2").rstrip("/"),
            api_key=os.getenv("DHAN_API_KEY", "").strip(),
            api_secret=os.getenv("DHAN_API_SECRET", "").strip(),
            auth_base_url=os.getenv("DHAN_AUTH_BASE_URL", "https://auth.dhan.co").rstrip("/"),
            token_expiry=os.getenv("DHAN_TOKEN_EXPIRY", "").strip(),
        ),
        risk=_build_risk_settings(),
    )


def _build_risk_settings() -> RiskSettings:
    from index_ai.risk_policy import effective_risk_limits

    mode = os.getenv("TRADING_MODE", "PAPER").strip().upper()
    if mode not in {"PAPER", "LIVE"}:
        mode = "PAPER"
    # Two independent locks. ALLOW_LIVE_TRADING was previously written but never
    # read here, so `mode == "LIVE"` alone armed real orders and the "second
    # confirmation" the dashboard advertised did not exist at any level.
    armed = os.getenv("ALLOW_LIVE_TRADING", "false").strip().lower() in {"1", "true", "yes", "on"}
    live_orders = mode == "LIVE" and armed
    p = HARDCODED_RISK
    limits = effective_risk_limits()
    return RiskSettings(
        trading_mode=mode,
        allow_live_trading=live_orders,
        allow_option_buying=p.allow_option_buying,
        allow_option_selling=p.allow_option_selling,
        max_losing_trades_per_day=int(
            limits["max_consecutive_losing_trades"] or p.max_losing_trades_per_day
        ),
        max_daily_loss_rupees=float(limits["max_daily_loss_rupees"]),
        trailing_stop_index_points=p.trailing_stop_index_points,
        min_confidence=p.min_confidence,
        max_profit_cap_rupees=limits["max_profit_cap_rupees"],
    )


ARM_LIVE_PHRASE = "ARM LIVE ORDERS"


def set_trading_mode(mode: str) -> str:
    """Switch PAPER (journal only) or LIVE (broker orders).

    Selecting LIVE does **not** by itself let orders leave the building: it sets
    TRADING_MODE only, and ``ALLOW_LIVE_TRADING`` stays as it was. Arming real
    orders is a separate, explicitly confirmed step (``arm_live_trading``).
    Previously one click set both flags, which meant a mis-click could send real
    money to the broker with no confirmation.

    Switching back to PAPER always disarms — the safe direction never needs
    ceremony.
    """
    normalized = mode.strip().upper()
    if normalized not in {"PAPER", "LIVE"}:
        raise ValueError("mode must be PAPER or LIVE")
    values = {"TRADING_MODE": normalized}
    if normalized == "PAPER":
        values["ALLOW_LIVE_TRADING"] = "false"
    update_env_values(values)
    return normalized


def arm_live_trading(confirm: str) -> bool:
    """Allow real broker orders. Requires the exact confirmation phrase."""
    if str(confirm or "").strip().upper() != ARM_LIVE_PHRASE:
        raise ValueError(f'Confirmation required: type "{ARM_LIVE_PHRASE}" exactly.')
    update_env_values({"ALLOW_LIVE_TRADING": "true"})
    return True


def disarm_live_trading() -> bool:
    """Block real broker orders. No confirmation — safety is always one click."""
    update_env_values({"ALLOW_LIVE_TRADING": "false"})
    return True


# Flags the app may toggle for itself, so operating it never needs hand-editing
# .env. Deliberately excludes anything that can move money.
TOGGLEABLE_FLAGS: dict[str, str] = {
    "ENABLE_TICK_FEED": "Live websocket tick feed (records exchange ticks to SQLite)",
    "ENABLE_MARKET_LOG": "Time-series log of observations and decisions",
    "ENABLE_SPREAD_SAMPLING": "Measure real option bid-ask during market hours",
    "OPTIONS_REQUIRE_VIABLE": "Block a credit-sell index whose measured gross edge can't cover its cost floor (off by default)",
    "ENABLE_FUTURES_PAPER": "Directional futures paper lane",
    "ENABLE_STOCK_FUTURES_PAPER": "Directional futures paper lane on the stock universe (20 NSE F&O names)",
    "ENABLE_BRAIN_GATE": "ML win-probability entry gate",
    "ENABLE_AI_COMMENTARY": "LLM session commentary (advisory only)",
    "AUTO_START_SCANNER": "Start the scanner automatically at launch",
    "ENABLE_S3_BACKUP": "Back up the journals, models and reports to S3 after the close",
    "ENABLE_SENSEX": "Scan and trade SENSEX (off = NIFTY + BANKNIFTY only)",
    "HIDE_BROKER_ACCOUNT": "Hide the real Dhan funds & broker positions panel (for a shared team view)",
    "HIDE_STRATEGY_BUILDER": "Hide the strategy Builder tab (not finished yet)",
}


def set_feature_flag(name: str, on: bool) -> dict[str, Any]:
    """Persist one non-financial feature flag to .env."""
    key = str(name or "").strip().upper()
    if key not in TOGGLEABLE_FLAGS:
        raise ValueError(f"{key} is not a toggleable feature flag")
    update_env_values({key: "true" if on else "false"})
    os.environ[key] = "true" if on else "false"
    return {"flag": key, "enabled": bool(on), "description": TOGGLEABLE_FLAGS[key]}


def feature_flags() -> list[dict[str, Any]]:
    out = []
    for key, desc in TOGGLEABLE_FLAGS.items():
        raw = os.getenv(key)
        default_on = key in {
            "ENABLE_MARKET_LOG",
            "ENABLE_SPREAD_SAMPLING",
            "ENABLE_FUTURES_PAPER",
            "ENABLE_STOCK_FUTURES_PAPER",
            "ENABLE_BRAIN_GATE",
            "AUTO_START_SCANNER",
            "ENABLE_SENSEX",
        }
        on = (raw.strip().lower() in {"1", "true", "yes", "on"}) if raw else default_on
        out.append({"flag": key, "enabled": on, "description": desc, "set_in_env": raw is not None})
    return out


_QUOTED_ENV_KEYS = frozenset(
    {
        "DHAN_ACCESS_TOKEN",
        "DHAN_API_SECRET",
        "DHAN_API_KEY",
    }
)


def _strip_env_quotes(value: str) -> str:
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"')
    return s


def repair_env_access_token_line() -> bool:
    """
    Fix .env files where a long JWT was split across multiple lines.
    Without this, dotenv only loads the first line and Dhan returns DH-906.
    """
    if not ENV_PATH.exists():
        return False
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    repaired = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("DHAN_ACCESS_TOKEN="):
            token = line.split("=", 1)[1].strip()
            token = _strip_env_quotes(token)
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt or nxt.startswith("#") or "=" in nxt:
                    break
                token += nxt
                repaired = True
                i += 1
            token = token.replace("\n", "").replace("\r", "").strip()
            out.append(f'DHAN_ACCESS_TOKEN="{token}"')
            continue
        out.append(line)
        i += 1
    if repaired:
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    return repaired


# .env writes are read-modify-write on a shared file. Without this lock two
# concurrent requests (two fast clicks on +) both read the old value and the
# second write silently discards the first — the UI counter and the real lot
# size then disagree, which on a trading system means orders sized wrong.
_ENV_WRITE_LOCK = threading.Lock()


def update_env_values(values: dict[str, str]) -> None:
    with _ENV_WRITE_LOCK:
        _update_env_values_locked(values)


def _update_env_values_locked(values: dict[str, str]) -> None:
    from dotenv import dotenv_values

    repair_env_access_token_line()
    header: list[str] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or not line.strip():
                header.append(line)

    merged: dict[str, str] = {}
    if ENV_PATH.exists():
        merged = {k: str(v) for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    merged.update(values)

    body: list[str] = []
    for key, value in merged.items():
        if key in _QUOTED_ENV_KEYS or len(value) > 120:
            safe = value.replace('"', "")
            body.append(f'{key}="{safe}"')
        else:
            body.append(f"{key}={value}")

    parts = header[:]
    if parts and parts[-1].strip():
        parts.append("")
    parts.extend(body)
    # Atomic: readers (settings() -> load_dotenv, polled from many threads) must
    # never see a half-written .env, or TRADING_MODE briefly reads as default.
    tmp = ENV_PATH.with_suffix(ENV_PATH.suffix + ".tmp")
    tmp.write_text("\n".join(parts) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)
