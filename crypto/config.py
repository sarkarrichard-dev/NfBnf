"""Crypto section configuration — Delta Exchange credentials + strategy knobs.

Read-only. The credentials endpoint in ``crypto.api`` is the only writer, and it
goes through ``index_ai.config.update_env_values`` (which preserves every other
key). Kept separate from ``index_ai.config`` on purpose: the crypto lane is its
own section with its own broker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from crypto._util import env_float as _f
from index_ai.config import MEMORY_DIR, _load_env

# crypto journals / caches live alongside the index ones
CRYPTO_MEMORY = MEMORY_DIR

DELTA_PROD_URL = "https://api.india.delta.exchange"

# The only coins this section will trade (Richard, 2026-09-08). The dashboard
# picklist is this list intersected with the perps Delta lists live
# (crypto/delta/products.available_symbols); USDT / USDC are the quote asset,
# not tradable perps, so they are not here. "PaxUsd" = PAXGUSD (PAX Gold).
CRYPTO_ALLOWLIST: tuple[str, ...] = (
    "BTCUSD", "ETHUSD", "BNBUSD", "XRPUSD", "SOLUSD",
    "TRXUSD", "DOGEUSD", "ADAUSD", "PAXGUSD",
)

# Default active set. Override at runtime with CRYPTO_SYMBOLS (a comma-separated
# list, validated against the allowlist). A symbol Delta does not list live is
# dropped by crypto/delta/products.py, never fabricated.
PERP_SYMBOLS: tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "PAXGUSD")


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    allow = set(CRYPTO_ALLOWLIST)
    out: list[str] = []
    for s in picked or PERP_SYMBOLS:
        if s in allow and s not in out:
            out.append(s)
    return tuple(out or PERP_SYMBOLS)


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
    leverage: float            # fixed 100x for crypto; clamped per-product at runtime
    max_concurrent: int        # open positions allowed PER STRATEGY (each strategy trades its own book)
    max_open_total: int        # portfolio-wide safety cap across all strategies; 0 = unlimited
    max_hold_days: int         # force-close a position open across more than this many day boundaries (crypto has no session)
    paper_bankroll_usd: float
    # lanes — the section runs when any strategy is enabled. ny_n_break and
    # ichimoku default on. The video strategies (bb_reversal / ema_jaguar /
    # vp_edge) default off: they turn on only after crypto/ml/optimize.py shows a
    # stable positive walk-forward net (per crypto/strategies/RESULTS.md).
    ny_nbreak_enabled: bool
    ichimoku_enabled: bool
    bb_reversal_enabled: bool
    ema_jaguar_enabled: bool
    vp_edge_enabled: bool
    # Lane-level trading window, IST, 24h "HH:MM". NEW ENTRIES fire only inside
    # this window (default 17:00–05:30 — the evening + overnight, US/crypto-active
    # hours); the daytime belongs to the Indian lanes. Open positions are managed
    # (trailing stop, TP, hold cap) around the clock regardless.
    session_start: str
    session_end: str
    # 6 PM (NY N-Break) session window, IST, 24h "HH:MM"
    ny_start: str
    ny_end: str
    # when true, ny_n_break takes the same N-break setup around the clock, not
    # only inside the NY window. Default OFF (2026-09-10): 24/7 N-breaks lost
    # money — Richard capped it back to its 18:00–23:00 window.
    nbreak_allround: bool
    # ichimoku
    ichimoku_tf: str
    # P&L trailing stop / target — percent of P&L on margin (see crypto/strategies/trailing.py)
    stop_pnl_pct: float
    ratchet_step_pnl_pct: float
    tp_trigger_pnl_pct: float
    peak_trail_pnl_pct: float
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
        leverage=max(1.0, _f("CRYPTO_LEVERAGE", 100.0)),
        max_concurrent=max(1, _i("CRYPTO_MAX_CONCURRENT", 2)),
        max_open_total=max(0, _i("CRYPTO_MAX_OPEN_TOTAL", 0)),
        max_hold_days=max(1, _i("CRYPTO_MAX_HOLD_DAYS", 1)),
        paper_bankroll_usd=max(100.0, _f("CRYPTO_PAPER_BANKROLL", 2000.0)),
        ny_nbreak_enabled=_b("CRYPTO_NY_NBREAK_ENABLED", True),
        ichimoku_enabled=_b("CRYPTO_ICHIMOKU_ENABLED", True),
        bb_reversal_enabled=_b("CRYPTO_BB_REVERSAL_ENABLED", False),
        ema_jaguar_enabled=_b("CRYPTO_EMA_JAGUAR_ENABLED", False),
        vp_edge_enabled=_b("CRYPTO_VP_EDGE_ENABLED", False),
        session_start=os.getenv("CRYPTO_SESSION_START", "16:00").strip(),
        session_end=os.getenv("CRYPTO_SESSION_END", "06:00").strip(),
        ny_start=os.getenv("CRYPTO_NY_START", "18:00").strip(),
        ny_end=os.getenv("CRYPTO_NY_END", "23:00").strip(),
        nbreak_allround=_b("CRYPTO_NBREAK_ALLROUND", False),
        ichimoku_tf=os.getenv("CRYPTO_ICHIMOKU_TF", "1h").strip(),
        stop_pnl_pct=max(0.0, _f("CRYPTO_STOP_PNL_PCT", 10.0)),
        ratchet_step_pnl_pct=max(0.5, _f("CRYPTO_RATCHET_STEP_PNL_PCT", 5.0)),
        tp_trigger_pnl_pct=max(1.0, _f("CRYPTO_TP_TRIGGER_PNL_PCT", 25.0)),
        peak_trail_pnl_pct=max(0.5, _f("CRYPTO_PEAK_TRAIL_PNL_PCT", 2.0)),
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
    "CRYPTO_NY_NBREAK_ENABLED",
    "CRYPTO_ICHIMOKU_ENABLED",
    "CRYPTO_BB_REVERSAL_ENABLED",
    "CRYPTO_EMA_JAGUAR_ENABLED",
    "CRYPTO_VP_EDGE_ENABLED",
    "CRYPTO_NBREAK_ALLROUND",
    "CRYPTO_NBREAK_MAX_TRADES",
    "CRYPTO_SESSION_START",
    "CRYPTO_SESSION_END",
    "CRYPTO_DEPLOY_USD",
    "CRYPTO_LEVERAGE",
    "CRYPTO_MAX_CONCURRENT",
    "CRYPTO_PAPER_BANKROLL",
    "CRYPTO_NY_START",
    "CRYPTO_NY_END",
    "CRYPTO_ICHIMOKU_TF",
    "CRYPTO_STOP_PNL_PCT",
    "CRYPTO_RATCHET_STEP_PNL_PCT",
    "CRYPTO_TP_TRIGGER_PNL_PCT",
    "CRYPTO_PEAK_TRAIL_PNL_PCT",
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
    assert s.max_concurrent >= 1 and s.max_open_total >= 0 and s.max_hold_days >= 1
    assert s.stop_pnl_pct > 0 and s.tp_trigger_pnl_pct > 0
    assert s.base_url.startswith("https://")
    assert not s.base_url.endswith("/")
    assert s.trading_mode in {"PAPER", "LIVE"}
    assert not s.live_orders_enabled or (s.trading_mode == "LIVE" and s.live_armed)
    assert s.symbols and all(x == x.upper() for x in s.symbols)
    assert len(set(s.symbols)) == len(s.symbols)  # deduped
    for w in (s.session_start, s.session_end, s.ny_start, s.ny_end):
        h, m = w.split(":")
        assert 0 <= int(h) < 24 and 0 <= int(m) < 60
    import os as _o
    _o.environ["CRYPTO_SYMBOLS"] = "btcusd, ethusd ,BTCUSD"
    assert _symbols() == ("BTCUSD", "ETHUSD")
    _o.environ.pop("CRYPTO_SYMBOLS")
    print("crypto.config self-check ok —", s.base_url, "symbols", s.symbols)
