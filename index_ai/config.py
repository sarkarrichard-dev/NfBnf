from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
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
    live_orders = mode == "LIVE"
    p = HARDCODED_RISK
    limits = effective_risk_limits()
    return RiskSettings(
        trading_mode=mode,
        allow_live_trading=live_orders,
        allow_option_buying=p.allow_option_buying,
        allow_option_selling=p.allow_option_selling,
        max_losing_trades_per_day=int(limits["max_consecutive_losing_trades"] or p.max_losing_trades_per_day),
        max_daily_loss_rupees=float(limits["max_daily_loss_rupees"]),
        trailing_stop_index_points=p.trailing_stop_index_points,
        min_confidence=p.min_confidence,
        max_profit_cap_rupees=limits["max_profit_cap_rupees"],
    )


def set_trading_mode(mode: str) -> str:
    """Only dashboard control: PAPER (journal) or LIVE (broker orders)."""
    normalized = mode.strip().upper()
    if normalized not in {"PAPER", "LIVE"}:
        raise ValueError("mode must be PAPER or LIVE")
    update_env_values(
        {
            "TRADING_MODE": normalized,
            "ALLOW_LIVE_TRADING": "true" if normalized == "LIVE" else "false",
        }
    )
    return normalized


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


def update_env_values(values: dict[str, str]) -> None:
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
    ENV_PATH.write_text("\n".join(parts) + "\n", encoding="utf-8")
