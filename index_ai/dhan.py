from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
import pandas as pd

from index_ai.config import DhanSettings
from index_ai.dhan_errors import (
    DhanAuthError,
    DhanRateLimitError,
    classify_http_error,
    explain_dhan_http_error,
    parse_dhan_error_payload,
)
from index_ai.instruments import IndexInstrument

# Dhan documents ~3 req/s for some feeds; stay conservative to avoid 429/805.
_MIN_REQUEST_INTERVAL_SEC = 0.65
_MAX_RETRIES = 4


class _RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


_limiter = _RateLimiter(_MIN_REQUEST_INTERVAL_SEC)


def unwrap_dhan_record_list(data: Any) -> list[dict[str, Any]]:
    """Normalize list payloads from Dhan trading APIs (array or nested under data)."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "orders", "orderBook", "orderList", "trades", "tradeBook", "positions"):
            block = data.get(key)
            if isinstance(block, list):
                return [x for x in block if isinstance(x, dict)]
    return []

# Dhan /charts/intraday: OHLC for last ~5 trading days only (not multi-week ranges).
INTRADAY_MAX_CALENDAR_DAYS = 5
_IST_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def clamp_intraday_date_range(
    from_date: str,
    to_date: str,
    *,
    max_calendar_days: int = INTRADAY_MAX_CALENDAR_DAYS,
) -> tuple[str, str]:
    """Keep intraday requests inside Dhan's allowed window."""
    end = datetime.strptime(to_date.strip(), _IST_DATETIME_FMT)
    start = datetime.strptime(from_date.strip(), _IST_DATETIME_FMT)
    if start > end:
        start = end - timedelta(days=1)
        start = start.replace(hour=9, minute=15, second=0)
    if (end.date() - start.date()).days > max_calendar_days:
        start = (end - timedelta(days=max_calendar_days)).replace(hour=9, minute=15, second=0)
    return start.strftime(_IST_DATETIME_FMT), end.strftime(_IST_DATETIME_FMT)


class DhanClient:
    def __init__(self, settings: DhanSettings) -> None:
        self.settings = settings

    def _headers(self, path: str = "") -> dict[str, str]:
        """Chart/historical APIs use access-token only; market feed & orders need client-id."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self.settings.access_token,
        }
        if not path.startswith("/charts/") and self.settings.client_id:
            headers["client-id"] = self.settings.client_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        context: str = "",
    ) -> dict[str, Any]:
        if not self.settings.ready:
            raise RuntimeError("Dhan credentials are missing. Put them in .env.")
        url = f"{self.settings.api_base_url}{path}"
        last_exc: BaseException | None = None
        auth_refreshed = False

        try:
            from index_ai.dhan_auth import auto_refresh_dhan_token

            refreshed = auto_refresh_dhan_token(
                self.settings,
                reason=f"preflight:{context or path}",
            )
            if refreshed.get("renewed"):
                self.settings = refreshed.get("settings") or self.settings
        except Exception:
            # Non-fatal: request still proceeds with current token.
            pass

        for attempt in range(_MAX_RETRIES):
            _limiter.wait()
            try:
                with httpx.Client(timeout=25) as client:
                    headers = self._headers(path)
                    if method.upper() == "GET":
                        response = client.get(url, headers=headers)
                    else:
                        response = client.post(url, headers=headers, json=payload or {})
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt + 1 < _MAX_RETRIES:
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
                    continue
                raise

            if response.status_code in {401, 807, 808, 809}:
                if not auth_refreshed:
                    try:
                        from index_ai.dhan_auth import auto_refresh_dhan_token

                        refreshed = auto_refresh_dhan_token(
                            self.settings,
                            force=True,
                            reason=f"auth_error:{context or path}",
                        )
                        if refreshed.get("renewed"):
                            self.settings = refreshed.get("settings") or self.settings
                            auth_refreshed = True
                            if attempt + 1 < _MAX_RETRIES:
                                continue
                    except Exception:
                        pass
                raise DhanAuthError(explain_dhan_http_error(response, context))
            if response.status_code in {429, 805}:
                last_exc = DhanRateLimitError(explain_dhan_http_error(response, context))
                if attempt + 1 < _MAX_RETRIES:
                    time.sleep(min(10.0, 2.0 * (2**attempt)))
                    continue
                raise last_exc

            if response.status_code >= 400:
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        hint = parse_dhan_error_payload(payload)
                        if hint:
                            label = f"{context}: " if context else ""
                            raise RuntimeError(f"{label}{hint}")
                except RuntimeError:
                    raise
                except Exception:
                    pass

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                classified = classify_http_error(exc, context)
                last_exc = classified
                if isinstance(classified, DhanRateLimitError) and attempt + 1 < _MAX_RETRIES:
                    time.sleep(min(10.0, 2.0 * (2**attempt)))
                    continue
                raise classified from exc

            try:
                data = response.json()
            except Exception as exc:
                raise RuntimeError(f"Dhan returned non-JSON for {path}") from exc
            return data if isinstance(data, dict) else {"data": data}

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Dhan request failed for {path}")

    def _post(self, path: str, payload: dict[str, Any], *, context: str = "") -> dict[str, Any]:
        return self._request("POST", path, payload=payload, context=context or path)

    def get_order(self, order_id: str) -> dict[str, Any]:
        oid = str(order_id or "").strip()
        if not oid:
            raise ValueError("order_id is required")
        return self._request("GET", f"/orders/{oid}", context="order status")

    def list_today_orders(self) -> list[dict[str, Any]]:
        """All orders for the current session (order book)."""
        data = self._request("GET", "/orders", context="order book")
        return unwrap_dhan_record_list(data)

    def get_fund_limits(self) -> dict[str, Any]:
        """Available balance, utilized margin, withdrawable (GET /fundlimit)."""
        return self._request("GET", "/fundlimit", context="fund limits")

    def list_today_trades(self) -> list[dict[str, Any]]:
        """Today's trade book from Dhan (executed fills, not journal)."""
        data = self._request("GET", "/trades", context="trade book")
        return unwrap_dhan_record_list(data)

    def trades_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """All fills for a single order id."""
        oid = str(order_id or "").strip()
        if not oid:
            raise ValueError("order_id is required")
        data = self._request("GET", f"/trades/{oid}", context="order trades")
        if isinstance(data, dict):
            return [data]
        return unwrap_dhan_record_list(data)

    def list_positions(self) -> list[dict[str, Any]]:
        """Open positions for the day (includes F&O carryforward)."""
        data = self._request("GET", "/positions", context="positions")
        return unwrap_dhan_record_list(data)

    def ltp(self, segment: str, security_ids: list[int]) -> dict[str, Any]:
        return self._post("/marketfeed/ltp", {segment: security_ids}, context="market LTP")

    def index_ltp(self, instrument: IndexInstrument) -> dict[str, Any]:
        if instrument.underlying_security_id is None:
            raise RuntimeError(f"{instrument.label} security id is not configured.")
        sid = str(instrument.underlying_security_id)
        raw = self.ltp(instrument.underlying_segment, [instrument.underlying_security_id])
        data = raw.get("data") or raw
        bucket = data.get(instrument.underlying_segment) or data.get(instrument.underlying_segment.lower()) or {}
        row = bucket.get(sid) or bucket.get(instrument.underlying_security_id) or {}
        last = row.get("last_price") or row.get("ltp") or row.get("lastPrice")
        if last is None and bucket:
            first = next(iter(bucket.values()), {})
            last = first.get("last_price") or first.get("ltp") or first.get("lastPrice")
        if last is None:
            raise RuntimeError(f"No LTP returned for {instrument.key}.")
        return {"last_price": float(last), "raw": raw}

    def ohlc(self, segment: str, security_ids: list[int]) -> dict[str, Any]:
        return self._post("/marketfeed/ohlc", {segment: security_ids}, context="market OHLC")

    def intraday_history(
        self,
        instrument: IndexInstrument,
        *,
        from_date: str,
        to_date: str,
        interval: str = "1",
    ) -> dict[str, Any]:
        if instrument.underlying_security_id is None:
            raise RuntimeError(f"{instrument.label} security id is not configured.")
        from_clamped, to_clamped = clamp_intraday_date_range(from_date, to_date)
        payload = {
            "securityId": str(instrument.underlying_security_id),
            "exchangeSegment": instrument.underlying_segment,
            "instrument": instrument.instrument_type,
            "interval": str(interval),
            "oi": False,
            "fromDate": from_clamped,
            "toDate": to_clamped,
        }
        return self._post("/charts/intraday", payload, context=f"{instrument.key} intraday chart")

    def historical_daily(
        self,
        instrument: IndexInstrument,
        *,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        if instrument.underlying_security_id is None:
            raise RuntimeError(f"{instrument.label} security id is not configured.")
        payload = {
            "securityId": str(instrument.underlying_security_id),
            "exchangeSegment": instrument.underlying_segment,
            "instrument": instrument.instrument_type,
            "expiryCode": 0,
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date,
        }
        return self._post("/charts/historical", payload, context=f"{instrument.key} daily chart")

    def expiry_list(self, instrument: IndexInstrument) -> list[str]:
        if instrument.underlying_security_id is None:
            raise RuntimeError(f"{instrument.label} security id is not configured.")
        payload = {
            "UnderlyingScrip": instrument.underlying_security_id,
            "UnderlyingSeg": instrument.underlying_segment,
        }
        data = self._post("/optionchain/expirylist", payload, context=f"{instrument.key} expiries")
        return list(data.get("data") or [])

    def option_chain(self, instrument: IndexInstrument, expiry: str) -> dict[str, Any]:
        if instrument.underlying_security_id is None:
            raise RuntimeError(f"{instrument.label} security id is not configured.")
        payload = {
            "UnderlyingScrip": instrument.underlying_security_id,
            "UnderlyingSeg": instrument.underlying_segment,
            "Expiry": expiry,
        }
        return self._post("/optionchain", payload, context=f"{instrument.key} option chain")

    def place_market_order(
        self,
        *,
        security_id: int,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        correlation_id: str,
        product_type: str | None = None,
    ) -> dict[str, Any]:
        from index_ai.dhan_orders import (
            build_market_order_payload,
            normalize_order_response,
            order_product_type_for_leg,
        )

        pt = product_type or order_product_type_for_leg(
            transaction_type=transaction_type,
            exchange_segment=exchange_segment,
        )
        payload = build_market_order_payload(
            client_id=str(self.settings.client_id),
            security_id=int(security_id),
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=int(quantity),
            correlation_id=correlation_id,
            product_type=pt,
        )
        raw = self._post("/orders", payload, context="place order")
        return normalize_order_response(raw)


def chart_response_to_frame(data: dict[str, Any]) -> pd.DataFrame:
    timestamps = data.get("timestamp") or data.get("t") or []
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(timestamps, unit="s", errors="coerce"),
            "open": data.get("open") or [],
            "high": data.get("high") or [],
            "low": data.get("low") or [],
            "close": data.get("close") or [],
            "volume": data.get("volume") or [],
        }
    )
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    return frame.reset_index(drop=True)
