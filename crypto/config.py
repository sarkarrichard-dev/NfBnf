"""Crypto section configuration — Delta Exchange credentials + strategy knobs.

Read-only. The credentials endpoint in ``crypto.api`` is the only writer, and it
goes through ``index_ai.config.update_env_values`` (which preserves every other
key). Kept separate from ``index_ai.config`` on purpose: the crypto lane is its
own section with its own broker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from index_ai.config import MEMORY_DIR, _load_env

# crypto journals / caches live alongside the index ones
CRYPTO_MEMORY = MEMORY_DIR

DELTA_PROD_URL = "https://api.india.delta.exchange"

# Perpetual contracts the crypto lane trades. Delta symbols; the numeric
# product_id is resolved at runtime from the contract master.
PERP_SYMBOLS: tuple[str, ...] = ("BTCUSD", "ETHUSD")


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CryptoSettings:
    api_key: str
    api_secret: str
    base_url: str
    # sizing (capital-first — see crypto/sizing.py in Phase 2)
    deploy_usd: float          # per-trade capital; hard floor 100
    leverage: float            # target leverage; clamped per-product at runtime
    max_concurrent: int
    paper_bankroll_usd: float
    allow_min_one: bool        # take 1 contract even if 1-contract margin > deploy_usd
    # lanes
    paper_enabled: bool
    ny_nbreak_enabled: bool
    ichimoku_enabled: bool
    # 6 PM (NY N-Break) session window, IST, 24h "HH:MM"
    ny_start: str
    ny_end: str
    # ichimoku
    ichimoku_tf: str
    # optional hard stops, percent of entry (0 = off)
    nbreak_sl_pct: float
    ichimoku_sl_pct: float

    @property
    def credentials_ready(self) -> bool:
        return bool(self.api_key and self.api_secret)


def crypto_settings() -> CryptoSettings:
    """Fresh read every call (like ``index_ai.config.settings``) so a saved key
    takes effect without a restart."""
    _load_env()
    return CryptoSettings(
        api_key=os.getenv("DELTA_API_KEY", "").strip(),
        api_secret=os.getenv("DELTA_API_SECRET", "").strip(),
        base_url=os.getenv("DELTA_BASE_URL", DELTA_PROD_URL).rstrip("/"),
        deploy_usd=max(100.0, _f("CRYPTO_DEPLOY_USD", 100.0)),
        leverage=max(1.0, _f("CRYPTO_LEVERAGE", 3.0)),
        max_concurrent=max(1, _i("CRYPTO_MAX_CONCURRENT", 2)),
        paper_bankroll_usd=max(100.0, _f("CRYPTO_PAPER_BANKROLL", 2000.0)),
        allow_min_one=_b("CRYPTO_ALLOW_MIN_ONE", False),
        paper_enabled=_b("ENABLE_CRYPTO_PAPER", False),
        ny_nbreak_enabled=_b("CRYPTO_NY_NBREAK_ENABLED", True),
        ichimoku_enabled=_b("CRYPTO_ICHIMOKU_ENABLED", True),
        ny_start=os.getenv("CRYPTO_NY_START", "18:00").strip(),
        ny_end=os.getenv("CRYPTO_NY_END", "23:00").strip(),
        ichimoku_tf=os.getenv("CRYPTO_ICHIMOKU_TF", "1h").strip(),
        nbreak_sl_pct=max(0.0, _f("CRYPTO_NBREAK_SL_PCT", 0.0)),
        ichimoku_sl_pct=max(0.0, _f("CRYPTO_ICHIMOKU_SL_PCT", 0.0)),
    )


CRYPTO_ENV_KEYS = (
    "DELTA_API_KEY",
    "DELTA_API_SECRET",
    "DELTA_BASE_URL",
    "ENABLE_CRYPTO_PAPER",
    "CRYPTO_NY_NBREAK_ENABLED",
    "CRYPTO_ICHIMOKU_ENABLED",
    "CRYPTO_DEPLOY_USD",
    "CRYPTO_LEVERAGE",
    "CRYPTO_MAX_CONCURRENT",
    "CRYPTO_PAPER_BANKROLL",
    "CRYPTO_ALLOW_MIN_ONE",
    "CRYPTO_NY_START",
    "CRYPTO_NY_END",
    "CRYPTO_ICHIMOKU_TF",
    "CRYPTO_NBREAK_SL_PCT",
    "CRYPTO_ICHIMOKU_SL_PCT",
)


if __name__ == "__main__":  # self-check
    s = crypto_settings()
    assert s.deploy_usd >= 100.0
    assert s.leverage >= 1.0
    assert s.base_url.startswith("https://")
    assert not s.base_url.endswith("/")
    print("crypto.config self-check ok —", s.base_url, "deploy", s.deploy_usd, "lev", s.leverage)
