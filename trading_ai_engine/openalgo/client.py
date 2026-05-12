from __future__ import annotations

from typing import Any

import httpx

from trading_ai_engine.openalgo.config import OpenAlgoConfig, load_openalgo_config, openalgo_orders_disabled_by_env


def placesmartorder_url(cfg: OpenAlgoConfig) -> str:
    return f"{cfg.base_url}/api/v1/placesmartorder"


def place_smart_order(
    *,
    symbol: str,
    exchange: str,
    action: str,
    quantity: int,
    position_size: float = 0.0,
    product: str,
    cfg: OpenAlgoConfig | None = None,
    pricetype: str = "MARKET",
) -> dict[str, Any]:
    """
    POST OpenAlgo ``/api/v1/placesmartorder`` (see OpenAlgo ``SmartOrderSchema``).

    Returns a dict with at least ``http_status`` and either broker ``status`` / ``orderid``
    or an ``error`` string on transport failure.
    """
    if openalgo_orders_disabled_by_env():
        return {"http_status": 0, "status": "error", "message": "TRADING_AI_OPENALGO_DISABLE is set"}
    c = cfg or load_openalgo_config()
    if not c.ready:
        return {"http_status": 0, "status": "error", "message": "OpenAlgo is not configured (OPENALGO_BASE_URL + OPENALGO_API_KEY)"}
    url = placesmartorder_url(c)
    body: dict[str, Any] = {
        "apikey": c.api_key,
        "strategy": c.strategy,
        "symbol": symbol,
        "exchange": exchange,
        "action": action.upper(),
        "quantity": int(quantity),
        "position_size": float(position_size),
        "pricetype": pricetype,
        "product": product.upper(),
        "price": 0.0,
        "trigger_price": 0.0,
        "disclosed_quantity": 0,
    }
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(url, json=body)
    except httpx.RequestError as e:
        return {"http_status": 0, "status": "error", "message": f"OpenAlgo request failed: {e}"}
    try:
        data = r.json()
    except Exception:
        data = {"status": "error", "message": r.text[:2000]}
    if isinstance(data, dict):
        out = dict(data)
        out["http_status"] = r.status_code
        return out
    return {"http_status": r.status_code, "status": "error", "message": "unexpected_response", "raw": data}


def openalgo_reachable_snapshot() -> dict[str, Any]:
    """Cheap readiness: config present + TCP connect to host:port (no auth probe)."""
    cfg = load_openalgo_config()
    if not cfg.base_url:
        return {"configured": False, "reachable": False, "detail": "invalid_or_empty_base_url"}
    if not cfg.api_key:
        return {"configured": False, "reachable": False, "detail": "missing_OPENALGO_API_KEY"}
    parsed = httpx.URL(cfg.base_url)
    host = parsed.host
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return {"configured": False, "reachable": False, "detail": "no_host"}
    try:
        with httpx.Client(timeout=3.0) as client:
            # HEAD may not be implemented; GET / is enough for "something is listening"
            r = client.get(f"{cfg.base_url}/", follow_redirects=True)
        ok = r.status_code < 600
    except httpx.RequestError:
        ok = False
    return {
        "configured": True,
        "reachable": ok,
        "host": host,
        "port": port,
        "scheme": parsed.scheme,
    }
