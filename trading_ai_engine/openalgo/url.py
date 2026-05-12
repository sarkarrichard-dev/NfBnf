"""Validate operator-configured OpenAlgo base URL (loopback / private LAN for http)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def normalize_openalgo_base_url(url: str) -> str:
    """
    Allow ``http`` for loopback and RFC1918-style private IPs, ``https`` broadly.
    Reject ``http`` to arbitrary public hostnames to avoid sending API keys in cleartext.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("OpenAlgo base URL is empty")
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"OpenAlgo base URL must be http or https (got {parsed.scheme!r})")
    host = parsed.hostname
    if not host:
        raise ValueError("OpenAlgo base URL has no hostname")
    h = host.lower()
    if h in ("0.0.0.0",):
        raise ValueError("OpenAlgo base URL host 0.0.0.0 is not allowed")

    try:
        ip = ipaddress.ip_address(h)
        if ip.is_multicast or ip.is_reserved:
            raise ValueError(f"OpenAlgo base URL host {host!r} is not allowed")
        if scheme == "http" and not (ip.is_loopback or ip.is_private):
            raise ValueError("OpenAlgo http is only allowed for loopback or private LAN IPs")
    except ValueError as e:
        if "OpenAlgo http" in str(e) or "not allowed" in str(e):
            raise
        # hostname (not a literal IP)
        if scheme == "http":
            if h in ("127.0.0.1", "localhost", "::1"):
                pass
            else:
                raise ValueError(
                    "OpenAlgo http to public hostnames is not allowed; use https or 127.0.0.1."
                ) from e

    return raw.rstrip("/")
