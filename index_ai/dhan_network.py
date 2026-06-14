"""Public IP lookup for Dhan Trading API whitelist setup."""

from __future__ import annotations

import time
from typing import Any

import httpx

_IP_CACHE: dict[str, Any] = {"ips": None, "fetched_at": 0.0}
_CACHE_TTL_SEC = 300.0


def _fetch_ip_from(url: str) -> str | None:
    try:
        with httpx.Client(timeout=6.0) as client:
            response = client.get(url)
            response.raise_for_status()
            if "json" in url:
                data = response.json()
                return str(data.get("ip") or "").strip() or None
            return (response.text or "").strip() or None
    except Exception:
        return None


def fetch_public_ips(*, force: bool = False) -> dict[str, str | None]:
    """IPv4 and IPv6 as seen from this machine — whitelist both on Dhan if both appear."""
    now = time.monotonic()
    cached = _IP_CACHE.get("ips")
    if not force and isinstance(cached, dict) and (now - float(_IP_CACHE["fetched_at"])) < _CACHE_TTL_SEC:
        return dict(cached)
    ips = {
        "ipv4": _fetch_ip_from("https://api4.ipify.org?format=json"),
        "ipv6": _fetch_ip_from("https://api6.ipify.org?format=json"),
    }
    _IP_CACHE["ips"] = ips
    _IP_CACHE["fetched_at"] = now
    return ips


def fetch_public_ip(*, force: bool = False) -> str | None:
    ips = fetch_public_ips(force=force)
    return ips.get("ipv4") or ips.get("ipv6")


def dhan_order_ip_whitelist_hint(public_ip: str | None = None) -> dict[str, Any]:
    ips = fetch_public_ips() if public_ip is None else {"ipv4": public_ip, "ipv6": None}
    listed = [ip for ip in (ips.get("ipv4"), ips.get("ipv6")) if ip]
    primary = public_ip or ips.get("ipv4") or ips.get("ipv6")
    return {
        "required_for": "POST /orders (live MARKET entries and exits)",
        "not_required_for": "Charts, option chain, and market LTP (your data access already works)",
        "public_ip": primary,
        "public_ips": ips,
        "whitelist_both": bool(ips.get("ipv4") and ips.get("ipv6")),
        "steps": [
            "Open web.dhan.co → My Profile → Access DhanHQ APIs → Static IP Setting",
            "Add every public IP this PC uses (IPv4 and IPv6 if both are shown below)",
            "Generate a fresh Access Token (24h) if status is Expired, paste into Index Options AI",
            "Restart the server, hard-refresh dashboard, run scanner in Live during 9:15–15:30 IST",
        ],
        "docs_url": "https://dhanhq.co/docs/v2/orders/",
    }
