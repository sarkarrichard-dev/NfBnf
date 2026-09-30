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


# ---------------------------------------------------------------------------
# Task 2 — every settle outcome (ORD-01 cancel race, D-07 hold-and-recheck)
# ---------------------------------------------------------------------------

_LEVERAGE_PATH = f"/v2/products/{PRODUCT_ID}/orders/leverage"
_UNREACHABLE_500 = (500, {"success": False, "error": {"code": "internal_server_error"}})


def _marker(raw_coid: str) -> dict:
    return {
        "since": "2026-09-07T18:05:30+00:00",
        "client_order_id": raw_coid,
        "product_id": PRODUCT_ID,
        "side": "long",
        "size": 4,
        "leverage": 20.0,
        "margin_total_usd": 100.0,
        "stop_price": 62000.0,
        "signal_price": 63000.0,
        "entry_time": FAKE_ENTRY_TS,
        "day": "2026-09-07",
        "entry_reason": "test signal (strategy logic not under test here)",
    }


def test_still_open_cancel_wins_records_nothing(tmp_path, monkeypatch):
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    expected_coid = _coid(raw_coid)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    open_row = {
        "id": 555200,
        "product_id": PRODUCT_ID,
        "client_order_id": expected_coid,
        "state": "open",
        "size": 4,
        "unfilled_size": 4,
        "side": "buy",
    }
    # 1st find_order call sees it still open; after the cancel, the 2nd call
    # (the re-read) no longer finds it among open orders.
    replay.script(
        "GET",
        "/v2/orders",
        {"success": True, "result": [open_row]},
        {"success": True, "result": []},
    )
    replay.script("DELETE", "/v2/orders", {"success": True, "result": {"id": 555200}})
    cancelled_row = dict(open_row, state="cancelled")
    replay.script("GET", "/v2/orders/history", {"success": True, "result": [cancelled_row]})

    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 1  # never resent
    assert replay.count("DELETE", "/v2/orders") == 1  # cancel issued exactly once
    assert replay.count("POST", _LEVERAGE_PATH) == 1  # no reduce-only exit POST followed
    assert any(e.get("event") == "live_rejected" for e in events)
    assert journal.load_state().get("ny_n_break:BTCUSD", {}).get("position") is None


def test_still_open_fill_wins_out_of_order_records_position(tmp_path, monkeypatch):
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    expected_coid = _coid(raw_coid)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    open_row = {
        "id": 555201,
        "product_id": PRODUCT_ID,
        "client_order_id": expected_coid,
        "state": "open",
        "size": 4,
        "unfilled_size": 4,
        "side": "buy",
    }
    replay.script(
        "GET",
        "/v2/orders",
        {"success": True, "result": [open_row]},
        {"success": True, "result": []},
    )
    # the cancel loses the race — Delta answers HTTP 400 because the order
    # already completed by the time the cancel reached it
    replay.script(
        "DELETE", "/v2/orders", (400, {"success": False, "error": {"code": "already_filled"}})
    )
    filled_row = dict(open_row, state="closed", unfilled_size=0)
    replay.script("GET", "/v2/orders/history", {"success": True, "result": [filled_row]})
    fill_row = {"order_id": 555201, "size": 4, "price": 61500.0}
    replay.script("GET", "/v2/fills", {"success": True, "result": [fill_row]})

    client = fake_delta_client(replay, monkeypatch)
    lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 1  # never resent
    pos = journal.load_state()["ny_n_break:BTCUSD"]["position"]
    assert pos is not None
    assert pos["mode"] == "live"
    assert pos["order_id"] == f"{PRODUCT_ID}:555201"
    assert pos["entry_price"] == 61500.0


def test_never_placed_records_nothing(tmp_path, monkeypatch):
    alerts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.script("GET", "/v2/orders", {"success": True, "result": []})
    replay.script("GET", "/v2/orders/history", {"success": True, "result": []})

    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 1  # never resent
    assert any(e.get("event") == "live_rejected" for e in events)
    assert journal.load_state().get("ny_n_break:BTCUSD", {}).get("position") is None
    assert any("BTCUSD" in text for text, _ in alerts)


def test_delta_unreachable_marks_unclear_entry_no_resend(tmp_path, monkeypatch):
    alerts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    replay.fault("POST", "/v2/orders", httpx.ReadTimeout)
    replay.script("GET", "/v2/orders", _UNREACHABLE_500)  # every lookup times out (D-07)

    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 1  # never resent
    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert slot.get("position") is None
    marker = slot.get("unclear_entry")
    assert marker is not None
    assert marker["client_order_id"] == f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    assert any(e.get("event") == "live_unconfirmed" for e in events)
    assert any("BTCUSD" in text for text, _ in alerts)


def test_next_scan_still_unreachable_not_stepped_no_resend(tmp_path, monkeypatch):
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": None,
                "unclear_entry": _marker(raw_coid),
            }
        }
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/orders", _UNREACHABLE_500)

    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 0  # nothing sent
    assert enter_event == {}  # the strategy was never stepped this scan
    assert any(
        e.get("event") == "wait" and "unconfirmed" in (e.get("reason") or "") for e in events
    )
    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert slot.get("unclear_entry") is not None
    assert slot.get("position") is None


def test_next_scan_adopts_filled_position(tmp_path, monkeypatch):
    alerts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    expected_coid = _coid(raw_coid)
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": None,
                "unclear_entry": _marker(raw_coid),
            }
        }
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/orders", {"success": True, "result": []})
    filled_row = {
        "id": 555300,
        "product_id": PRODUCT_ID,
        "client_order_id": expected_coid,
        "state": "closed",
        "size": 4,
        "unfilled_size": 0,
        "side": "buy",
    }
    replay.script("GET", "/v2/orders/history", {"success": True, "result": [filled_row]})
    fill_row = {"order_id": 555300, "size": 4, "price": 61750.0}
    replay.script("GET", "/v2/fills", {"success": True, "result": [fill_row]})

    client = fake_delta_client(replay, monkeypatch)
    lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 0  # confirmed by lookup only, never resent
    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert "unclear_entry" not in slot
    pos = slot["position"]
    assert pos is not None
    assert pos["mode"] == "live"
    assert pos["order_id"] == f"{PRODUCT_ID}:555300"
    assert pos["entry_price"] == 61750.0
    assert any("BTCUSD" in text for text, _ in alerts)


def test_next_scan_clears_marker_when_never_existed(tmp_path, monkeypatch):
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": None,
                "unclear_entry": _marker(raw_coid),
            }
        }
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/orders", {"success": True, "result": []})
    replay.script("GET", "/v2/orders/history", {"success": True, "result": []})

    client = fake_delta_client(replay, monkeypatch)
    lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 0
    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert "unclear_entry" not in slot
    assert slot.get("position") is None


def test_definite_rejection_never_calls_settle_entry(tmp_path, monkeypatch):
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    def boom(*_a, **_k):
        raise RuntimeError("delta 400: insufficient margin")

    monkeypatch.setattr(lanes.executor, "place_entry", boom)

    def must_not_settle(*_a, **_k):
        raise AssertionError("settle_entry must not be called for a definite rejection")

    monkeypatch.setattr(lanes.executor, "settle_entry", must_not_settle)

    replay = BrokerReplay(load_traffic("delta_rest"))
    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 0
    assert any(e.get("event") == "live_rejected" for e in events)
    assert journal.load_state().get("ny_n_break:BTCUSD", {}).get("position") is None
