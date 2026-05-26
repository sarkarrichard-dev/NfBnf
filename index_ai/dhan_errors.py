from __future__ import annotations

import httpx


class DhanAuthError(RuntimeError):
    """Access token invalid or expired — user must re-login."""


class DhanRateLimitError(RuntimeError):
    """Too many Dhan API calls — back off and retry."""


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
