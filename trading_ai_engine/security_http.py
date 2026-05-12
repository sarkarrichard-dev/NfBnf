"""HTTP client safety helpers (SSRF-style abuse of configurable upstream URLs)."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_PLAIN_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_plain_yyyy_mm_dd(s: str) -> bool:
    return bool(_PLAIN_DATE.match(s.strip()[:10]))


def validate_https_public_url(url: str, *, purpose: str = "upstream") -> str:
    """
    Require ``https`` and reject loopback / private / link-local literal hosts.

    Hostnames are not resolved (DNS rebinding is out of scope); literal IPs are checked.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError(f"{purpose} URL is empty")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{purpose} URL must use https (got scheme {parsed.scheme!r})")
    host = parsed.hostname
    if not host:
        raise ValueError(f"{purpose} URL has no hostname")
    h = host.lower()
    if h in ("localhost", "0.0.0.0") or h.endswith(".local"):
        raise ValueError(f"{purpose} URL hostname {host!r} is not allowed")
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"{purpose} URL host {host!r} is not a reachable public endpoint")
    except ValueError as e:
        if "not a reachable" in str(e):
            raise
        # not a literal IP — hostname allowed
    return raw.rstrip("/")
