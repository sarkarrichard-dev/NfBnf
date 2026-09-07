"""Crypto section HTTP surface — mounted at /api/crypto by index_ai.server.

All handlers are plain ``def`` so Starlette runs them in a threadpool: they do
blocking I/O (httpx to Delta, an ``.env`` write) which must never touch the
event loop (CLAUDE.md).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, HTTPException

from crypto.config import PERP_SYMBOLS, crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import DeltaClient, DeltaError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


def _mask(secret: str) -> str:
    s = secret or ""
    if len(s) <= 8:
        return "set" if s else ""
    return f"{s[:4]}…{s[-2:]}"


@router.get("/status", include_in_schema=False)
def crypto_status() -> dict:
    s = crypto_settings()
    return {
        "credentials_ready": s.credentials_ready,
        "api_key_preview": _mask(s.api_key),
        "base_url": s.base_url,
        "paper_enabled": s.paper_enabled,
        "lanes": {"ny_n_break": s.ny_nbreak_enabled, "ichimoku": s.ichimoku_enabled},
        "sizing": {
            "deploy_usd": s.deploy_usd,
            "leverage": s.leverage,
            "max_concurrent": s.max_concurrent,
            "paper_bankroll_usd": s.paper_bankroll_usd,
            "allow_min_one": s.allow_min_one,
        },
        "session_ist": {"start": s.ny_start, "end": s.ny_end},
        "ichimoku_tf": s.ichimoku_tf,
        "hard_stops_pct": {"ny_n_break": s.nbreak_sl_pct, "ichimoku": s.ichimoku_sl_pct},
        "symbols": list(PERP_SYMBOLS),
    }


def _health_blocking() -> dict:
    s = crypto_settings()
    out: dict = {
        "connected": False,
        "credentials_ready": s.credentials_ready,
        "base_url": s.base_url,
        "wallet_usd": None,
        "wallet_inr": None,
        "quotes": {},
        "errors": [],
    }
    client = DeltaClient(s)

    for sym in PERP_SYMBOLS:
        try:
            tk = market_data.ticker(sym, client=client)
            q = tk.get("quotes") or {}
            out["quotes"][sym] = {
                "mark": _num(tk.get("mark_price")),
                "bid": _num(q.get("best_bid")),
                "ask": _num(q.get("best_ask")),
            }
        except DeltaError as exc:
            out["errors"].append(f"{sym} quote: {exc}")

    if not s.credentials_ready:
        out["errors"].append("Delta API key/secret not set — add them in the Crypto Setup panel.")
        return out

    try:
        bal = client.wallet()
        out["wallet_usd"] = round(sum(_num(w.get("balance")) or 0.0 for w in bal), 2)
        out["wallet_inr"] = round(sum(_num(w.get("balance_inr")) or 0.0 for w in bal), 2)
        out["connected"] = True
    except DeltaError as exc:
        out["errors"].append(f"wallet: {exc}")
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/health", include_in_schema=False)
def crypto_health() -> dict:
    return _health_blocking()


@router.get("/contracts", include_in_schema=False)
def crypto_contracts() -> dict:
    try:
        cs = products.all_contracts()
    except DeltaError as exc:
        raise HTTPException(502, f"Delta products fetch failed: {exc}") from exc
    return {
        sym: {
            "product_id": c.product_id,
            "contract_value": c.contract_value,
            "tick_size": c.tick_size,
            "min_size": c.min_size,
            "max_leverage": c.max_leverage,
            "usable": c.usable,
        }
        for sym, c in cs.items()
    }


@router.post("/credentials", include_in_schema=False)
def set_credentials(
    api_key: str = Body(..., embed=True),
    api_secret: str = Body(..., embed=True),
    confirm: bool = Body(False, embed=True),
) -> dict:
    """Save Delta API credentials to .env. Money-path — requires confirm=true.

    These keys can place real orders once live execution ships, so they do NOT
    go through the generic feature-toggle endpoint — same treatment as the Dhan
    login.
    """
    if not confirm:
        raise HTTPException(400, "Set confirm=true to save Delta credentials.")
    key, sec = api_key.strip(), api_secret.strip()
    if not key or not sec:
        raise HTTPException(400, "Both api_key and api_secret are required.")

    from index_ai.config import update_env_values

    update_env_values({"DELTA_API_KEY": key, "DELTA_API_SECRET": sec})
    os.environ["DELTA_API_KEY"] = key
    os.environ["DELTA_API_SECRET"] = sec

    check = _health_blocking()
    return {
        "saved": True,
        "api_key_preview": _mask(key),
        "connected": check["connected"],
        "wallet_usd": check["wallet_usd"],
        "wallet_inr": check["wallet_inr"],
        "errors": check["errors"],
    }
