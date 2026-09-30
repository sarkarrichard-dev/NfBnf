"""Crypto live-order settlement (D-06/D-07/ORD-01): a lost Delta entry reply
must be resolved by asking Delta's own order list, never by re-sending the
order — proven end to end on replayed, redacted real Delta traffic through
the real DeltaClient/executor/lane code (D-03)."""

from __future__ import annotations

import httpx

from _fake_brokers import (
    FAKE_ENTRY_TS,
    BrokerReplay,
    fake_delta_client,
    load_traffic,
    setup_live_crypto_lane,
)
from crypto import journal, lanes
from crypto.executor import _coid

PRODUCT_ID = 27  # matches the BTC Contract set up by setup_live_crypto_lane


def test_lost_entry_reply_filled_on_delta_records_one_position(tmp_path, monkeypatch):
    alerts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )

    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    expected_coid = _coid(raw_coid)

    replay = BrokerReplay(load_traffic("delta_rest"))
    # Leverage POST: reference-shaped success (place_entry never inspects the
    # body, only that it doesn't raise) — see
    # reference/openalgo/broker/deltaexchange/api/order_api.py:374-400.
    replay.script(
        "POST",
        f"/v2/products/{PRODUCT_ID}/orders/leverage",
        {"success": True, "result": {"leverage": "20"}},
    )
    # The entry POST's reply never comes back — a transport-level fault, not
    # a definite HTTP answer (D-06).
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    # No open orders left on Delta for this client_order_id — it already
    # settled. Shaped ("reference"), not recorded: the live account's real
    # capture hit an IP-whitelist error, so there is no real empty-open-orders
    # row to reuse (see tests/fixtures/broker_traffic/delta_rest.jsonl).
    replay.script("GET", "/v2/orders", {"success": True, "result": []})
    order_row = {
        "id": 555123,
        "product_id": PRODUCT_ID,
        "client_order_id": expected_coid,
        "state": "closed",
        "size": 4,
        "unfilled_size": 0,
        "side": "buy",
    }
    replay.script("GET", "/v2/orders/history", {"success": True, "result": [order_row]})
    fill_row = {"order_id": 555123, "size": 4, "price": 61987.5}
    replay.script("GET", "/v2/fills", {"success": True, "result": [fill_row]})

    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert enter_event.get("fired") is True
    assert replay.count("POST", "/v2/orders") == 1  # never resent (D-07)

    state = journal.load_state()
    pos = state["ny_n_break:BTCUSD"]["position"]
    assert pos is not None, f"no position recorded — events: {events}"
    assert pos["mode"] == "live"
    assert pos["order_id"] == f"{PRODUCT_ID}:555123"
    assert pos["entry_price"] == 61987.5
    assert pos["entry_price_source"] == "fill"

    assert any("BTCUSD" in text for text, _ in alerts), f"no alert mentioned the symbol: {alerts}"
