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
        try:
            from crypto import notify

            notify.alert(
                f"\U0001f6d1 <b>CRYPTO KILL SWITCH</b> — live disarmed\n{why}",
                key="kill_switch", gap_s=3600,
            )
        except Exception:
            pass
        return False, f"kill switch: {why}"
    return True, ""


# --- orders -----------------------------------------------------------------

def _set_leverage(client: DeltaClient, product_id: int, leverage: float) -> None:
    lev = str(int(round(max(1.0, leverage))))
    client.signed(
        "POST", f"/v2/products/{product_id}/orders/leverage", body={"leverage": lev}
    )


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
    client: DeltaClient, contract: Contract, side: str, size: int,
    *, client_order_id: str | None = None,
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


# --- reconciliation --------------------------------------------------------

def reconcile(client: DeltaClient) -> list[str]:
    """Compare crypto_state.json **live** open positions against Delta's live
    positions. Paper positions are never a Delta mismatch. Reports only — never
    trades to 'fix' one. Trust Delta."""
    st = journal.load_state()
    local = {
        v["position"].get("asset"): k
        for k, v in st.items()
        if ":" in k and isinstance(v, dict) and v.get("position")
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
            from crypto import notify

            notify.alert(
                "⚠️ <b>CRYPTO RECONCILE</b>\n" + "\n".join(issues),
                key="reconcile", gap_s=3600,
            )
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
        journal.journal({"mode": "live", "day": datetime.now(timezone.utc).date().isoformat(),
                         "pnl_usd": -5.0, "exit_time": datetime.now(timezone.utc).isoformat()})

    class _S:
        max_daily_loss_usd = 50.0
        max_consec_losses = 3
    trip, why = kill_switch(_S())
    assert trip and "consecutive" in why
    print("crypto.executor self-check ok")
