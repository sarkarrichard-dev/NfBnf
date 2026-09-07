"""Signed Delta Exchange India REST client.

Ported from ``reference/openalgo/broker/deltaexchange/api/{baseurl,order_api}.py``.

Sync (``httpx.Client``). **Never call from an ``async def`` handler** — wrap in
``asyncio.to_thread`` (CLAUDE.md: blocking I/O in an async handler has bitten
this project twice).

Signature prehash is ``METHOD + timestamp + path + query_string + body`` and
Delta rejects it 5 s after creation, so the request is signed *inside* the retry
loop — every attempt re-signs with a fresh timestamp.
"""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import logging
import time
from typing import Any

import httpx

from crypto.config import CryptoSettings, crypto_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0        # signed calls (orders, wallet, margin)
_PUBLIC_TIMEOUT = 8.0  # public market data — kept short so telemetry can't stall the scan loop
_MAX_RETRIES = 3
_MAX_429_WAIT = 8.0
_USER_AGENT = "algo-bnf-crypto/1"


class DeltaError(RuntimeError):
    """A Delta API call failed — HTTP error, auth failure, or ``success: false``."""


def _sign(secret: str, prehash: str) -> str:
    return hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).hexdigest()


def _query_string(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    # sorted so the signed string and the sent URL always match exactly
    return "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def _reset_wait(headers: httpx.Headers) -> float:
    """Seconds to wait after a 429, from Delta's own reset hint (ms), capped."""
    for name, scale in (("x-rate-limit-reset", 1000.0), ("retry-after", 1.0)):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            return min(max(float(raw) / scale, 0.5), _MAX_429_WAIT)
        except ValueError:
            pass
    return 1.0


class DeltaClient:
    def __init__(self, settings: CryptoSettings | None = None) -> None:
        s = settings or crypto_settings()
        self._base = s.base_url
        self._key = s.api_key
        self._secret = s.api_secret

    @property
    def has_credentials(self) -> bool:
        return bool(self._key and self._secret)

    # ---- public (unauthenticated) -----------------------------------------
    def get_public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params, signed=False)

    # ---- signed ---------------------------------------------------------------
    def signed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        return self._request(method, path, params=params, body=body, signed=True)

    # convenience read helpers (Phase 1 uses only these)
    def wallet(self) -> list[dict[str, Any]]:
        return self.signed("GET", "/v2/wallet/balances")

    def positions(self) -> list[dict[str, Any]]:
        return self.signed("GET", "/v2/positions/margined")

    def fills(self) -> list[dict[str, Any]]:
        return self.signed("GET", "/v2/fills")

    def margin_required(
        self, product_id: int, size: int, side: str, order_type: str = "market_order"
    ) -> dict[str, Any]:
        return self.signed(
            "GET",
            f"/v2/products/{product_id}/margin_required",
            params={"size": size, "side": side, "order_type": order_type},
        )

    # ---- internals ----------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        signed: bool = False,
    ) -> Any:
        m = method.upper()
        qs = _query_string(params)
        url = f"{self._base}{path}{qs}"
        payload = _json.dumps(body, separators=(",", ":")) if body is not None else ""

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
            if signed:
                if not (self._key and self._secret):
                    raise DeltaError("Delta API key/secret not configured")
                ts = str(int(time.time()))
                headers.update(
                    {
                        "api-key": self._key,
                        "timestamp": ts,
                        "signature": _sign(self._secret, m + ts + path + qs + payload),
                        "Content-Type": "application/json",
                    }
                )
            try:
                with httpx.Client(timeout=_TIMEOUT if signed else _PUBLIC_TIMEOUT) as client:
                    resp = client.request(m, url, headers=headers, content=payload or None)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= _MAX_RETRIES:
                    raise DeltaError(f"{m} {path}: {exc}") from exc
                time.sleep(0.3 * (attempt + 1))
                continue

            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                wait = _reset_wait(resp.headers)
                logger.warning("Delta 429 on %s — retrying in %.1fs", path, wait)
                time.sleep(wait)
                continue

            try:
                data = resp.json()
            except ValueError as exc:
                raise DeltaError(
                    f"{m} {path}: non-JSON response (HTTP {resp.status_code})"
                ) from exc

            if resp.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
                err = data.get("error") if isinstance(data, dict) else data
                raise DeltaError(f"{m} {path}: HTTP {resp.status_code} — {err}")

            return data.get("result", data) if isinstance(data, dict) else data

        raise DeltaError(f"{m} {path}: retries exhausted ({last_exc})")


if __name__ == "__main__":  # self-check — no network
    assert _query_string(None) == ""
    assert _query_string({"b": 2, "a": 1}) == "?a=1&b=2"
    # signature is deterministic for a fixed timestamp
    sig = _sign("secret", "GET" + "1700000000" + "/v2/wallet/balances" + "" + "")
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)
    print("crypto.delta.client self-check ok — signing + query string")
