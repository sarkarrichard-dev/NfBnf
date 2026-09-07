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

# Default perpetual contracts. Override at runtime with CRYPTO_SYMBOLS (a
# comma-separated list) — see crypto_settings().symbols. A symbol Delta does not
# list live is dropped by crypto/delta/products.py, never fabricated.
PERP_SYMBOLS: tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "PAXGUSD")


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


def _mode(name: str) -> str:
    m = os.getenv(name, "PAPER").strip().upper()
    return m if m in {"PAPER", "LIVE"} else "PAPER"


def _symbols() -> tuple[str, ...]:
    raw = os.getenv("CRYPTO_SYMBOLS", "")
    picked = [x.strip().upper() for x in raw.split(",") if x.strip()]
    out: list[str] = []
    for s in picked or PERP_SYMBOLS:
        if s not in out:
            out.append(s)
    return tuple(out)


@dataclass(frozen=True)
class CryptoSettings:
    api_key: str
    api_secret: str
    base_url: str
    force_ipv4: bool           # pin Delta traffic to IPv4 (CRYPTO_FORCE_IPV4)
    symbols: tuple[str, ...]   # perps to trade — CRYPTO_SYMBOLS
    # sizing (lot-based — see crypto/sizing.py). One universal lot count; 1 lot =
    # 1 Delta contract, so every symbol trades `lots` contracts.
    lots: int                  # universal lot count, min 1
    deploy_usd: float          # optional per-trade margin cap in USD; 0 = no cap
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
    # live execution (Phase 4) — two independent locks, see crypto/live.py
    trading_mode: str          # PAPER (default) | LIVE
    live_armed: bool           # CRYPTO_ALLOW_LIVE
    max_daily_loss_usd: float
    max_consec_losses: int

    @property
    def credentials_ready(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def live_orders_enabled(self) -> bool:
        """The only gate the executor trusts. All three must hold."""
        return (
            self.trading_mode.upper() == "LIVE"
            and self.live_armed
            and self.credentials_ready
        )


def crypto_settings() -> CryptoSettings:
    """Fresh read every call (like ``index_ai.config.settings``) so a saved key
    takes effect without a restart."""
    _load_env()
    return CryptoSettings(
        api_key=os.getenv("DELTA_API_KEY", "").strip(),
        api_secret=os.getenv("DELTA_API_SECRET", "").strip(),
        base_url=os.getenv("DELTA_BASE_URL", DELTA_PROD_URL).rstrip("/"),
        force_ipv4=_b("CRYPTO_FORCE_IPV4", True),
        symbols=_symbols(),
        lots=max(1, _i("CRYPTO_LOTS", 1)),
        deploy_usd=max(0.0, _f("CRYPTO_DEPLOY_USD", 0.0)),
        leverage=max(1.0, _f("CRYPTO_LEVERAGE", 3.0)),
        max_concurrent=max(1, _i("CRYPTO_MAX_CONCURRENT", 2)),
        paper_bankroll_usd=max(100.0, _f("CRYPTO_PAPER_BANKROLL", 2000.0)),
        allow_min_one=_b("CRYPTO_ALLOW_MIN_ONE", False),
        paper_enabled=_b("ENABLE_CRYPTO_PAPER", True),
        ny_nbreak_enabled=_b("CRYPTO_NY_NBREAK_ENABLED", True),
        ichimoku_enabled=_b("CRYPTO_ICHIMOKU_ENABLED", True),
        ny_start=os.getenv("CRYPTO_NY_START", "18:00").strip(),
        ny_end=os.getenv("CRYPTO_NY_END", "23:00").strip(),
        ichimoku_tf=os.getenv("CRYPTO_ICHIMOKU_TF", "1h").strip(),
        nbreak_sl_pct=max(0.0, _f("CRYPTO_NBREAK_SL_PCT", 0.0)),
        ichimoku_sl_pct=max(0.0, _f("CRYPTO_ICHIMOKU_SL_PCT", 0.0)),
        trading_mode=_mode("CRYPTO_TRADING_MODE"),
        live_armed=_b("CRYPTO_ALLOW_LIVE", False),
        max_daily_loss_usd=abs(_f("CRYPTO_MAX_DAILY_LOSS_USD", 50.0)),
        max_consec_losses=max(1, _i("CRYPTO_MAX_CONSEC_LOSSES", 3)),
    )


CRYPTO_ENV_KEYS = (
    "DELTA_API_KEY",
    "DELTA_API_SECRET",
    "DELTA_BASE_URL",
    "CRYPTO_FORCE_IPV4",
    "CRYPTO_SYMBOLS",
    "CRYPTO_LOTS",
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
    "CRYPTO_TRADING_MODE",
    "CRYPTO_ALLOW_LIVE",
    "CRYPTO_MAX_DAILY_LOSS_USD",
    "CRYPTO_MAX_CONSEC_LOSSES",
)


if __name__ == "__main__":  # self-check
    s = crypto_settings()
    assert s.lots >= 1
    assert s.deploy_usd >= 0.0
    assert s.leverage >= 1.0
    assert s.base_url.startswith("https://")
    assert not s.base_url.endswith("/")
    assert s.trading_mode in {"PAPER", "LIVE"}
    assert not s.live_orders_enabled or (s.trading_mode == "LIVE" and s.live_armed)
    assert s.symbols and all(x == x.upper() for x in s.symbols)
    assert len(set(s.symbols)) == len(s.symbols)  # deduped
    import os as _o
    _o.environ["CRYPTO_SYMBOLS"] = "btcusd, ethusd ,BTCUSD"
    assert _symbols() == ("BTCUSD", "ETHUSD")
    _o.environ.pop("CRYPTO_SYMBOLS")
    print("crypto.config self-check ok —", s.base_url, "symbols", s.symbols)
