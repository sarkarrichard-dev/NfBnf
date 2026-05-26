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
DASHBOARD_DIR = ROOT / "dashboard"


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


def settings() -> AppSettings:
    load_dotenv(ENV_PATH, override=True)
    try:
        from index_ai.dhan_auth import reconcile_env_with_jwt

        reconcile_env_with_jwt()
        load_dotenv(ENV_PATH, override=True)
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
    mode = os.getenv("TRADING_MODE", "PAPER").strip().upper()
    if mode not in {"PAPER", "LIVE"}:
        mode = "PAPER"
    live_orders = mode == "LIVE"
    p = HARDCODED_RISK
    return RiskSettings(
        trading_mode=mode,
        allow_live_trading=live_orders,
        allow_option_buying=p.allow_option_buying,
        allow_option_selling=p.allow_option_selling,
        max_losing_trades_per_day=p.max_losing_trades_per_day,
        max_daily_loss_rupees=p.max_daily_loss_rupees,
        trailing_stop_index_points=p.trailing_stop_index_points,
        min_confidence=p.min_confidence,
        max_profit_cap_rupees=p.max_profit_cap_rupees,
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


def update_env_values(values: dict[str, str]) -> None:
    existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(values)
    output: list[str] = []
    for line in existing:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _ = line.split("=", 1)
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")
