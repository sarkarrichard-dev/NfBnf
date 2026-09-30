"""Crypto broker-outage safety (ORD-01, ORD-02, D-08, D-09): Close-all must not
miss a position mid-placement, and a Delta outage must never be read as
"closed" — it is held, escalated to a plain alert after three minutes, and
reconciled exactly once when Delta answers again. Real client code through
``fake_delta_client`` (D-03); only the network hop is faked."""

from __future__ import annotations

import threading
import time

from _fake_brokers import (
    Block,
    BrokerReplay,
    FAKE_ENTRY_TS,
    fake_delta_client,
    load_traffic,
    setup_live_crypto_lane,
)
from crypto import journal, lanes

PRODUCT_ID = 27  # matches the BTC Contract set up by setup_live_crypto_lane
_LEVERAGE_PATH = f"/v2/products/{PRODUCT_ID}/orders/leverage"


def _reduce_only(calls) -> list:
    return [
        c
        for c in calls
        if c[0] == "POST" and c[1] == "/v2/orders" and (c[2] or {}).get("reduce_only")
    ]


def _entry_only(calls) -> list:
    return [
        c
        for c in calls
        if c[0] == "POST" and c[1] == "/v2/orders" and not (c[2] or {}).get("reduce_only")
    ]


# ---------------------------------------------------------------------------
# Task 1 — Close-all / Close must not miss a position being opened right now
# ---------------------------------------------------------------------------


def test_close_all_waits_for_inflight_entry_then_closes_it(tmp_path, monkeypatch):
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    block = Block()
    replay.fault("POST", "/v2/orders", block)
    replay.script(
        "POST",
        "/v2/orders",
        {"success": True, "result": {"id": 700, "product_id": PRODUCT_ID, "state": "closed"}},
        {"success": True, "result": {"id": 701, "product_id": PRODUCT_ID, "state": "closed"}},
    )
    replay.script("GET", "/v2/fills", {"success": True, "result": []})

    client = fake_delta_client(replay, monkeypatch)

    scan_events: list = []

    def run_scan() -> None:
        scan_events.extend(lanes.scan_crypto_paper(client))

    t_a = threading.Thread(target=run_scan)
    t_a.start()

    close_result: dict = {}
    t_b = None
    try:
        assert block.entered.wait(timeout=5.0), "the entry POST never reached the fake broker"

        def run_close_all() -> None:
            close_result.update(lanes.close_all_positions_manual(client))

        t_b = threading.Thread(target=run_close_all)
        t_b.start()

        time.sleep(0.5)
        assert t_b.is_alive(), (
            "close-all returned before the in-flight entry finished — it missed the lock"
        )
    finally:
        # ALWAYS unblock and join — a still-running background thread using
        # this test's monkeypatched fixtures would corrupt the next test the
        # instant those patches are torn down, regardless of what failed above.
        block.release.set()
        t_a.join(timeout=10.0)
        if t_b is not None:
            t_b.join(timeout=10.0)

    assert not t_a.is_alive(), "scan thread deadlocked"
    assert t_b is not None and not t_b.is_alive(), "close-all thread deadlocked"

    assert "ny_n_break:BTCUSD" in close_result.get("closed", [])
    assert len(_entry_only(replay.calls)) == 1
    assert len(_reduce_only(replay.calls)) == 1

    state = journal.load_state()
    assert state.get("ny_n_break:BTCUSD", {}).get("position") is None
    rows = journal.recent()
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "manual close"


def test_close_position_manual_waits_for_inflight_entry_then_closes_it(tmp_path, monkeypatch):
    """Regression lock: the dashboard's single Close already held _STATE_LOCK
    (it always has) — this proves the race fix to close_all didn't disturb it."""
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("POST", _LEVERAGE_PATH, {"success": True, "result": {"leverage": "20"}})
    block = Block()
    replay.fault("POST", "/v2/orders", block)
    replay.script(
        "POST",
        "/v2/orders",
        {"success": True, "result": {"id": 710, "product_id": PRODUCT_ID, "state": "closed"}},
        {"success": True, "result": {"id": 711, "product_id": PRODUCT_ID, "state": "closed"}},
    )
    replay.script("GET", "/v2/fills", {"success": True, "result": []})

    client = fake_delta_client(replay, monkeypatch)

    def run_scan() -> None:
        lanes.scan_crypto_paper(client)

    t_a = threading.Thread(target=run_scan)
    t_a.start()

    close_result: dict = {}
    t_b = None
    try:
        assert block.entered.wait(timeout=5.0)

        def run_close() -> None:
            close_result.update(lanes.close_position_manual("ny_n_break:BTCUSD", client))

        t_b = threading.Thread(target=run_close)
        t_b.start()

        time.sleep(0.5)
        assert t_b.is_alive()
    finally:
        block.release.set()
        t_a.join(timeout=10.0)
        if t_b is not None:
            t_b.join(timeout=10.0)

    assert not t_a.is_alive() and t_b is not None and not t_b.is_alive()

    # a retry loop, not a crash: before the lock is free, close_position_manual
    # sees "no open position yet" and t_b would return ok=False — since it
    # blocks on _STATE_LOCK until the entry is saved, it must succeed instead.
    assert close_result.get("ok") is True
    assert len(_entry_only(replay.calls)) == 1
    assert len(_reduce_only(replay.calls)) == 1
    assert journal.load_state().get("ny_n_break:BTCUSD", {}).get("position") is None


def test_close_all_nothing_in_flight_behaves_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 63000.0})
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "position": {
                    "strategy": "ny_n_break",
                    "asset": "BTCUSD",
                    "side": "long",
                    "day": "2026-09-07",
                    "mode": "paper",
                    "entry_price": 62000.0,
                    "entry_price_source": "signal",
                    "entry_time": FAKE_ENTRY_TS,
                    "size": 1,
                    "contract_value": 0.001,
                    "leverage": 20.0,
                    "margin_total_usd": 50.0,
                    "notional_usd": 62.0,
                    "stop_price": 61000.0,
                    "opened_at": FAKE_ENTRY_TS,
                    "order_id": None,
                    "entry_reason": "test",
                    "features": {},
                }
            }
        }
    )
    result = lanes.close_all_positions_manual()
    assert result["ok"] is True
    assert result["attempted"] == 1
    assert result["closed"] == ["ny_n_break:BTCUSD"]
    assert result["failed"] == {}
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None

    # nothing open -> a clean no-op
    empty = lanes.close_all_positions_manual()
    assert empty == {"ok": True, "attempted": 0, "closed": [], "failed": {}}
