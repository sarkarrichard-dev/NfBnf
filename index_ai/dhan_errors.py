from __future__ import annotations

import json
from typing import Any

import httpx


class DhanAuthError(RuntimeError):
    """Access token invalid or expired — user must re-login."""


class DhanRateLimitError(RuntimeError):
    """Too many Dhan API calls — back off and retry."""


def parse_dhan_error_payload(data: dict[str, Any]) -> str | None:
    code = str(data.get("errorCode") or data.get("error_code") or "").strip()
    msg = str(data.get("errorMessage") or data.get("message") or "").strip()
    if code == "DH-906" or "invalid token" in msg.lower():
        return (
            "Invalid Token (DH-906). Generate a new access token on web.dhan.co "
            "(My Profile → Access DhanHQ APIs) and Save Token."
        )
    if code == "DH-812":
        return "Invalid date format (DH-812). Use YYYY-MM-DD HH:MM:SS in IST."
    if code == "DH-813":
        return (
            "Invalid securityId (DH-813). Set NIFTY_SECURITY_ID / BANKNIFTY_SECURITY_ID "
            "in .env from Dhan's instrument master."
        )
    if code in {"DH-814", "DH-904"}:
        return (
            f"{msg or code} — intraday charts allow at most ~5 trading days per request; "
            "the app now clamps the range automatically."
        )
    if code == "806":
        return "Data API not subscribed (DH-806). Enable Data API on Dhan Web."
    if msg:
        return f"{code}: {msg}" if code else msg
    return None


def explain_dhan_http_error(response: httpx.Response, context: str = "") -> str:
    code = response.status_code
    body = (response.text or "")[:300]
    prefix = f"{context}: " if context else ""

    if code in {401, 807, 808, 809}:
        return (
            f"{prefix}Dhan access token expired or invalid (HTTP {code}). "
            "Open Dhan Login → Create Login Link → paste a fresh tokenId into the dashboard."
        )
    if code in {429, 805}:
        return (
            f"{prefix}Dhan rate limit (HTTP {code}). The scanner will slow down and retry. "
            "Avoid running multiple apps on the same token."
        )
    if code == 806:
        return (
            f"{prefix}Dhan Data API not subscribed on your account (HTTP 806). "
            "Enable market data on web.dhan.co → Access DhanHQ APIs."
        )
    try:
        data = response.json()
        if isinstance(data, dict):
            friendly = parse_dhan_error_payload(data)
            if friendly:
                return f"{prefix}{friendly}"
    except (json.JSONDecodeError, ValueError):
        pass
    if code == 400:
        return (
            f"{prefix}Bad request (HTTP 400). {body} "
            "Intraday history is limited to ~5 trading days on Dhan."
        )
    if body:
        return f"{prefix}Dhan API HTTP {code}: {body}"
    return f"{prefix}Dhan API HTTP {code}"


def classify_http_error(exc: BaseException, context: str = "") -> BaseException:
    if isinstance(exc, httpx.HTTPStatusError):
        msg = explain_dhan_http_error(exc.response, context)
        if exc.response.status_code in {401, 807, 808, 809}:
            return DhanAuthError(msg)
        if exc.response.status_code in {429, 805}:
            return DhanRateLimitError(msg)
    return exc
