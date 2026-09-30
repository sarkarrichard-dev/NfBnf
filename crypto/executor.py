"""Live order placement on Delta Exchange — Phase 4.

Every function raises on hard failure; ``crypto.lanes`` catches and decides.
Nothing here runs unless ``CryptoSettings.live_orders_enabled`` is true (LIVE
mode + armed + credentials). This module is only ever called from inside the
already-threaded ``scan_crypto_paper`` — never from an async handler.

Delta order payloads follow ``reference/openalgo/broker/deltaexchange/`` —
market orders, ``reduce_only`` exits, a separate pre-order leverage call, and
composite order ids ``{product_id}:{order_id}``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from crypto import journal
from crypto.config import CryptoSettings, crypto_settings
from crypto.delta.client import DeltaClient, DeltaError
from crypto.delta.products import Contract
from crypto.live import disarm_crypto_live

logger = logging.getLogger(__name__)


# --- kill switch --------------------------------------------------------------


def _today_live_rows() -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = []
    for r in journal.recent(500):
        if str(r.get("mode")) != "live":
            continue
        stamp = str(r.get("closed_at") or r.get("exit_time") or r.get("day") or "")[:10]
        if stamp == today:
            rows.append(r)
    return rows


def kill_switch(settings: CryptoSettings | None = None) -> tuple[bool, str]:
    """(tripped, reason). Reads today's *live* closed trades from the journal."""
    s = settings or crypto_settings()
    from index_ai.risk_manager import stopped as account_stopped

    hit, why = account_stopped()  # India + crypto live losses, one ₹ budget
    if hit:
        return True, why
    rows = _today_live_rows()
    if not rows:
        return False, ""
    net = sum(float(r.get("pnl_usd") or 0.0) for r in rows)
    if net <= -abs(s.max_daily_loss_usd):
        return True, f"daily loss ${-net:,.2f} >= ${s.max_daily_loss_usd:,.0f} limit"
    streak = 0
    for r in reversed(rows):  # rows are oldest-first
        if float(r.get("pnl_usd") or 0.0) < 0:
            streak += 1
        else:
            break
    if streak >= s.max_consec_losses:
        return True, f"{streak} consecutive live losses (limit {s.max_consec_losses})"
    return False, ""


def live_gate(settings: CryptoSettings | None = None) -> tuple[bool, str]:
    """Call before every live entry. Blocks (and auto-disarms) on the kill switch."""
    s = settings or crypto_settings()
    if not s.live_orders_enabled:
        return False, "live orders not enabled (mode / arm / credentials)"
    tripped, why = kill_switch(s)
    if tripped:
        disarm_crypto_live()
        logger.error("crypto KILL SWITCH — live disarmed: %s", why)
        try:
            from index_ai import notify

            notify.alert(
                f"\U0001f6d1 <b>CRYPTO KILL SWITCH</b> — live disarmed\n{why}",
                key="kill_switch",
            )
        except Exception:
            pass
        return False, f"kill switch: {why}"
    return True, ""


# --- orders -----------------------------------------------------------------


def _set_leverage(client: DeltaClient, product_id: int, leverage: float) -> None:
    lev = str(int(round(max(1.0, leverage))))
    client.signed("POST", f"/v2/products/{product_id}/orders/leverage", body={"leverage": lev})


def _place(client: DeltaClient, body: dict[str, Any]) -> dict[str, Any]:
    res = client.signed("POST", "/v2/orders", body=body)
    oid = res.get("id")
    pid = res.get("product_id") or body.get("product_id")
    return {
        "order_id": f"{pid}:{oid}" if oid is not None else None,
        "raw_order_id": oid,
        "state": res.get("state"),
        "product_id": pid,
        "raw": res,
    }


def _coid(*parts: Any) -> str:
    """Deterministic client_order_id so a transport-level retry is de-duped by
    Delta rather than placing a second order. <= 40 chars."""
    raw = "-".join(str(p) for p in parts if p is not None)
    return raw.replace(" ", "").replace(":", "")[:40]


def place_entry(
    client: DeltaClient,
    contract: Contract,
    side: str,
    size: int,
    *,
    leverage: float,
    sl_price: float | None = None,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Market entry. Sets leverage first, then POST /v2/orders. Raises on failure."""
    if size < 1:
        raise ValueError(f"refusing to place a {size}-contract order")
    _set_leverage(client, contract.product_id, leverage)
    body: dict[str, Any] = {
        "product_id": contract.product_id,
        "product_symbol": contract.symbol,
        "size": int(size),
        "side": "buy" if side == "long" else "sell",
        "order_type": "market_order",
        "time_in_force": "ioc",
    }
    if client_order_id:
        body["client_order_id"] = _coid(client_order_id)
    if sl_price and sl_price > 0:
        body["bracket_stop_loss_price"] = str(round(float(sl_price), 2))
    return _place(client, body)


def place_exit(
    client: DeltaClient,
    contract: Contract,
    side: str,
    size: int,
    *,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Reduce-only market order that closes a `side` position of `size` contracts."""
    body = {
        "product_id": contract.product_id,
        "product_symbol": contract.symbol,
        "size": int(abs(size)),
        "side": "sell" if side == "long" else "buy",  # opposite side
        "order_type": "market_order",
        "time_in_force": "ioc",
        "reduce_only": True,
    }
    if client_order_id:
        body["client_order_id"] = _coid(client_order_id)
    return _place(client, body)


def position_state(client: DeltaClient, symbol: str) -> str:
    """'open' | 'flat' | 'unknown' for `symbol` on Delta right now.
    'unknown' means the API call failed — the caller must NOT treat that as flat."""
    try:
        for p in client.positions() or []:
            if str(p.get("product_symbol")) == symbol:
                return "open" if abs(float(p.get("size") or 0)) > 0 else "flat"
        return "flat"
    except Exception:
        return "unknown"


def fill_report(client: DeltaClient, order_id: str | None) -> tuple[float | None, float]:
    """(size-weighted avg price, total filled size) for an order id. (None, 0) on failure."""
    if not order_id:
        return None, 0.0
    raw = str(order_id).split(":")[-1]
    try:
        fills = client.signed("GET", "/v2/fills")
    except DeltaError:
        return None, 0.0
    num = px = 0.0
    for f in fills or []:
        if isinstance(f, dict) and str(f.get("order_id")) == raw:
            try:
                q = abs(float(f.get("size") or 0))
                p = float(f.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if q and p:
                num += p * q
                px += q
    return (round(num / px, 2) if px else None), px


def fill_price(client: DeltaClient, order_id: str | None) -> float | None:
    """Size-weighted average fill for an order id ({pid}:{id} or bare id)."""
    if not order_id:
        return None
    raw = str(order_id).split(":")[-1]
    try:
        fills = client.signed("GET", "/v2/fills")
    except DeltaError:
        return None
    num = px = 0.0
    for f in fills or []:
        if str(f.get("order_id")) == raw:
            q = abs(float(f.get("size") or 0))
            p = float(f.get("price") or 0)
            if q and p:
                num += p * q
                px += q
    return round(num / px, 2) if px else None


# --- settlement (D-06/D-07: a lost entry reply is asked of Delta, never resent) --


def outcome_unknown(exc: Exception) -> bool:
    """True only when `exc` is a DeltaError whose reply never came back (the
    order may still have reached Delta) or a 5xx. Everything else — a 4xx, a
    `success: false`, or any non-Delta exception — is a definite answer."""
    if not isinstance(exc, DeltaError):
        return False
    if isinstance(exc.__cause__, httpx.TransportError):
        return True
    return exc.status is not None and exc.status >= 500


def find_order(client: DeltaClient, client_order_id: str, product_id: int) -> dict[str, Any] | None:
    """The row (from open orders, then order history) whose client_order_id
    and product_id match ours, or None. Lets DeltaError propagate — the
    caller decides what "could not ask" means."""
    for rows in (client.open_orders(), client.order_history()):
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("client_order_id")) == client_order_id and int(
                row.get("product_id") or -1
            ) == int(product_id):
                return row
    return None


def settle_entry(client: DeltaClient, *, client_order_id: str, product_id: int) -> dict[str, Any]:
    """Ask Delta what happened to an entry whose own POST reply was lost.
    Never re-sends the order. Returns verdict FILLED | NOT_PLACED | UNKNOWN,
    order_id ({product_id}:{id} or None), filled (contracts), cancelled, error."""
    coid = _coid(client_order_id)
    out: dict[str, Any] = {
        "verdict": "UNKNOWN",
        "order_id": None,
        "filled": 0.0,
        "cancelled": False,
        "error": None,
    }
    try:
        row = find_order(client, coid, product_id)
    except DeltaError as exc:
        out["error"] = str(exc)
        return out
    if row is None:
        out["verdict"] = "NOT_PLACED"
        return out

    def _unfilled(r: dict[str, Any]) -> float:
        try:
            return abs(float(r.get("unfilled_size") or 0))
        except (TypeError, ValueError):
            return 0.0

    state = str(row.get("state") or "").lower()
    if state in {"open", "pending"} and _unfilled(row) > 0:
        # Still working on Delta — cancel it. A cancel can lose the race to a
        # fill (the order completes between our lookup and the DELETE); the
        # RE-READ decides the outcome, never the cancel call's own reply.
        oid = row.get("id")
        try:
            if oid is not None:
                client.cancel_order(int(oid), product_id)
        except DeltaError:
            pass
        out["cancelled"] = True
        try:
            row = find_order(client, coid, product_id)
        except DeltaError as exc:
            out["error"] = str(exc)
            return out
        if row is None:
            out["verdict"] = "NOT_PLACED"
            return out

    oid = row.get("id")
    out["order_id"] = f"{product_id}:{oid}" if oid is not None else None
    try:
        size = abs(float(row.get("size") or 0))
    except (TypeError, ValueError):
        size = 0.0
    out["filled"] = max(0.0, size - _unfilled(row))
    out["verdict"] = "FILLED" if out["filled"] > 0 else "NOT_PLACED"
    return out


# --- reconciliation --------------------------------------------------------


def reconcile(client: DeltaClient) -> list[str]:
    """Compare crypto_state.json **live** open positions against Delta's live
    positions. Paper positions are never a Delta mismatch. Reports only — never
    trades to 'fix' one. Trust Delta."""
    st = journal.load_state()
    local = {
        v["position"].get("asset"): k
        for k, v in st.items()
        if ":" in k
        and isinstance(v, dict)
        and v.get("position")
        and str((v["position"] or {}).get("mode")) == "live"
    }
    try:
        delta_open = {
            str(p.get("product_symbol"))
            for p in (client.positions() or [])
            if abs(float(p.get("size") or 0)) > 0
        }
    except DeltaError as exc:
        return [f"could not read Delta positions: {exc}"]

    issues: list[str] = []
    for sym, key in local.items():
        if sym not in delta_open:
            issues.append(f"local {key} open but Delta shows FLAT for {sym}")
    for sym in delta_open:
        if sym not in local:
            issues.append(f"Delta holds {sym} but no local paper/live position tracks it")
    if issues:
        logger.error("crypto reconcile mismatch: %s", " | ".join(issues))
        try:
            from index_ai import notify

            notify.alert("⚠️ <b>CRYPTO RECONCILE</b>\n" + "\n".join(issues), key="reconcile")
        except Exception:
            pass
    return issues


if __name__ == "__main__":  # self-check — mocked client, no network, no journal write
    import tempfile
    from pathlib import Path

    journal.JOURNAL_PATH = Path(tempfile.mkdtemp()) / "j.jsonl"

    class _Client:
        def __init__(self):
            self.calls = []

        def signed(self, method, path, *, params=None, body=None):
            self.calls.append((method, path, body))
            if path.endswith("/leverage"):
                return {"leverage": body["leverage"]}
            if path == "/v2/orders":
                return {"id": 999, "product_id": body["product_id"], "state": "closed"}
            if path == "/v2/fills":
                return [{"order_id": 999, "size": 3, "price": 63000.0}]
            return {}

    c = _Client()
    btc = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    r = place_entry(c, btc, "short", 3, leverage=3, sl_price=64000)
    assert r["order_id"] == "27:999"
    assert c.calls[0][1].endswith("/leverage") and c.calls[0][2] == {"leverage": "3"}
    assert c.calls[1][2]["side"] == "sell" and c.calls[1][2]["order_type"] == "market_order"
    assert c.calls[1][2]["bracket_stop_loss_price"] == "64000.0"
    assert fill_price(c, "27:999") == 63000.0
    x = place_exit(c, btc, "short", 3)
    assert x["order_id"] == "27:999" and c.calls[-1][2]["reduce_only"] is True
    assert c.calls[-1][2]["side"] == "buy"  # opposite of a short

    # kill switch: 3 straight live losses trips it
    for i in range(3):
        journal.journal(
            {
                "mode": "live",
                "day": datetime.now(timezone.utc).date().isoformat(),
                "pnl_usd": -5.0,
                "exit_time": datetime.now(timezone.utc).isoformat(),
            }
        )

    class _S:
        max_daily_loss_usd = 50.0
        max_consec_losses = 3

    trip, why = kill_switch(_S())
    assert trip and "consecutive" in why

    # outcome_unknown: transport error / 5xx are unknown, everything else is definite
    cause = httpx.ConnectTimeout("timed out")
    assert outcome_unknown(DeltaError("boom")) is False  # bare error, no cause/status
    lost_reply = DeltaError("POST /v2/orders: boom")
    lost_reply.__cause__ = cause
    assert outcome_unknown(lost_reply) is True
    assert outcome_unknown(DeltaError("boom", status=502)) is True
    assert outcome_unknown(DeltaError("boom", status=400)) is False
    assert outcome_unknown(ValueError("not even a DeltaError")) is False

    # find_order / settle_entry: tracer scope — lookup only, never re-sends
    class _SettleClient:
        def open_orders(self):
            return []

        def order_history(self):
            return [
                {
                    "id": 55,
                    "product_id": 27,
                    "client_order_id": "abc",
                    "size": 3,
                    "unfilled_size": 0,
                },
            ]

    sc = _SettleClient()
    assert find_order(sc, "abc", 27)["id"] == 55
    assert find_order(sc, "missing", 27) is None
    res = settle_entry(sc, client_order_id="abc", product_id=27)
    assert res["verdict"] == "FILLED" and res["order_id"] == "27:55" and res["filled"] == 3.0
    res2 = settle_entry(sc, client_order_id="nope", product_id=27)
    assert res2["verdict"] == "NOT_PLACED"

    # settle_entry: a still-open order is cancelled, then the re-read decides
    class _CancelWinsClient:
        def __init__(self):
            self.cancels = []
            self._calls = 0

        def open_orders(self):
            self._calls += 1
            if self._calls == 1:
                return [
                    {
                        "id": 9,
                        "product_id": 27,
                        "client_order_id": "x",
                        "size": 2,
                        "unfilled_size": 2,
                        "state": "open",
                    }
                ]
            return []  # cancelled -- no longer open

        def order_history(self):
            return [
                {
                    "id": 9,
                    "product_id": 27,
                    "client_order_id": "x",
                    "size": 2,
                    "unfilled_size": 2,
                    "state": "cancelled",
                }
            ]

        def cancel_order(self, order_id, product_id):
            self.cancels.append((order_id, product_id))

    cw = _CancelWinsClient()
    res3 = settle_entry(cw, client_order_id="x", product_id=27)
    assert res3["verdict"] == "NOT_PLACED" and res3["cancelled"] is True and cw.cancels == [(9, 27)]

    class _FillWinsClient:
        """The cancel loses the race -- DeltaError on cancel, re-read shows filled."""

        def __init__(self):
            self._calls = 0

        def open_orders(self):
            self._calls += 1
            if self._calls == 1:
                return [
                    {
                        "id": 10,
                        "product_id": 27,
                        "client_order_id": "y",
                        "size": 2,
                        "unfilled_size": 2,
                        "state": "open",
                    }
                ]
            return []

        def order_history(self):
            return [
                {
                    "id": 10,
                    "product_id": 27,
                    "client_order_id": "y",
                    "size": 2,
                    "unfilled_size": 0,
                    "state": "closed",
                }
            ]

        def cancel_order(self, order_id, product_id):
            raise DeltaError("DELETE /v2/orders: HTTP 400 — already filled", status=400)

    res4 = settle_entry(_FillWinsClient(), client_order_id="y", product_id=27)
    assert res4["verdict"] == "FILLED" and res4["filled"] == 2.0 and res4["order_id"] == "27:10"

    print("crypto.executor self-check ok")
