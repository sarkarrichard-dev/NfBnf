"""Crypto broker-outage safety (ORD-01, ORD-02, D-08, D-09): Close-all must not
miss a position mid-placement, and a Delta outage must never be read as
"closed" — it is held, escalated to a plain alert after three minutes, and
reconciled exactly once when Delta answers again. Real client code through
``fake_delta_client`` (D-03); only the network hop is faked."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from _fake_brokers import (
    Block,
    BrokerReplay,
    FAKE_ENTRY_TS,
    fake_delta_client,
    load_traffic,
    setup_live_crypto_lane,
)
from crypto import executor, journal, lanes

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


# ---------------------------------------------------------------------------
# Task 2 — a Delta outage is held, escalated after 180s, reconciled once
# ---------------------------------------------------------------------------


def _marker(raw_coid: str, since: str) -> dict:
    return {
        "since": since,
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


def _live_pos(**overrides) -> dict:
    # opened_at defaults to "now" (not FAKE_ENTRY_TS, which is 2026-09-07) so
    # the lane's own max-hold-days force-close never fires mid-test and masks
    # the outage behavior this file is actually exercising.
    pos = {
        "strategy": "ny_n_break",
        "asset": "BTCUSD",
        "side": "long",
        "day": "2026-09-07",
        "mode": "live",
        "entry_price": 63000.0,
        "entry_price_source": "fill",
        "entry_time": FAKE_ENTRY_TS,
        "size": 4,
        "contract_value": 0.001,
        "leverage": 20.0,
        "margin_total_usd": 100.0,
        "notional_usd": 252.0,
        "stop_price": 62000.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "order_id": "27:999",
        "entry_reason": "test",
        "features": {},
    }
    pos.update(overrides)
    return pos


def test_outage_while_holding_keeps_position_no_exit_no_alert_yet(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    journal.save_state(
        {"ny_n_break:BTCUSD": {"strategy": {"seeded": True}, "position": _live_pos()}}
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/positions/margined", (504, {"success": False, "error": {}}))
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            k.get("state"),
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )

    client = fake_delta_client(replay, monkeypatch)
    lanes.scan_crypto_paper(client)

    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert slot["position"] is not None
    assert slot["position"].get("unknown_since") is not None
    assert len(_reduce_only(replay.calls)) == 0
    assert journal.recent() == []
    assert alerts == []


def test_outage_escalates_after_180s_then_deduplicates(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    stale_since = (datetime.now(timezone.utc) - timedelta(seconds=181)).isoformat()
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": _live_pos(unknown_since=stale_since),
            }
        }
    )
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            {"seeded": True},
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/positions/margined", (504, {"success": False, "error": {}}))
    client = fake_delta_client(replay, monkeypatch)

    lanes.scan_crypto_paper(client)
    assert len(alerts) == 1
    assert "BTCUSD" in alerts[0][0] and "3 minutes" in alerts[0][0]
    key1 = alerts[0][1]

    # A second scan re-sends with the SAME key — this stub bypasses the real
    # on-disk window, so it can't prove the window itself (that's
    # tests/test_notify.py's job); it proves the lane keeps using one stable
    # key per position, which is what the real de-dup keys on.
    lanes.scan_crypto_paper(client)
    assert len(alerts) == 2
    assert alerts[1][1] == key1


def test_reconnect_still_open_clears_marker_no_alert(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": _live_pos(unknown_since="2026-09-07T18:00:00+00:00"),
            }
        }
    )
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            {"seeded": True},
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script(
        "GET",
        "/v2/positions/margined",
        {"success": True, "result": [{"product_symbol": "BTCUSD", "size": 4}]},
    )
    client = fake_delta_client(replay, monkeypatch)
    lanes.scan_crypto_paper(client)

    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert slot["position"] is not None
    assert "unknown_since" not in slot["position"]
    assert alerts == []
    assert journal.recent() == []


def test_reconnect_flat_reaps_once_no_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": _live_pos(unknown_since="2026-09-07T18:00:00+00:00"),
            }
        }
    )
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            {"seeded": True},
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )
    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 63500.0})

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/positions/margined", {"success": True, "result": []})
    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert any(e.get("event") == "reaped" for e in events)
    rows = journal.recent()
    assert len(rows) == 1
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None

    # a further scan (nothing left to reap) journals nothing more
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            {"position": None},
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )
    lanes.scan_crypto_paper(client)
    assert len(journal.recent()) == 1


def test_unconfirmed_entry_stuck_escalates_once_still_skipped(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    raw_coid = f"ny_n_break-BTCUSD-{FAKE_ENTRY_TS}"
    stale_since = (datetime.now(timezone.utc) - timedelta(seconds=181)).isoformat()
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"seeded": True},
                "position": None,
                "unclear_entry": _marker(raw_coid, stale_since),
            }
        }
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/orders", (504, {"success": False, "error": {}}))
    client = fake_delta_client(replay, monkeypatch)
    events = lanes.scan_crypto_paper(client)

    assert replay.count("POST", "/v2/orders") == 0
    assert len(alerts) == 1
    assert "BTCUSD" in alerts[0][0]
    slot = journal.load_state()["ny_n_break:BTCUSD"]
    assert slot.get("unclear_entry") is not None
    assert any(e.get("event") == "wait" for e in events)


def test_reconcile_unreadable_not_marked_done_retried_next_scan(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    # captured BEFORE setup_live_crypto_lane stubs executor.reconcile to a
    # no-op (lanes.executor IS crypto.executor — the same module object, so
    # reading `executor.reconcile` after the stub is applied would just hand
    # back the stub). This test is specifically about the real
    # reconcile()/RECONCILE_UNREADABLE path.
    real_reconcile = executor.reconcile
    enter_event: dict = {}
    setup_live_crypto_lane(monkeypatch, tmp_path, enter_event=enter_event)
    monkeypatch.setattr(lanes.executor, "reconcile", real_reconcile)
    journal.save_state(
        {"ny_n_break:BTCUSD": {"strategy": {"seeded": True}, "position": _live_pos()}}
    )
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: (
            {"seeded": True},
            {"strategy": "ny_n_break", "asset": "BTCUSD", "event": "hold"},
        ),
    )

    replay = BrokerReplay(load_traffic("delta_rest"))
    replay.script("GET", "/v2/positions/margined", (504, {"success": False, "error": {}}))
    client = fake_delta_client(replay, monkeypatch)

    lanes.scan_crypto_paper(client)
    st = journal.load_state()
    assert st.get("_live_reconciled") is None
    assert any("reconcile-unreadable" in (k or "") for _, k in alerts)

    # readable next scan -> marker IS set now
    replay2 = BrokerReplay(load_traffic("delta_rest"))
    replay2.script(
        "GET",
        "/v2/positions/margined",
        {"success": True, "result": [{"product_symbol": "BTCUSD", "size": 4}]},
    )
    client2 = fake_delta_client(replay2, monkeypatch)
    lanes.scan_crypto_paper(client2)
    st2 = journal.load_state()
    assert st2.get("_live_reconciled") is not None


def test_reconcile_unreadable_no_alert_when_nothing_local_live(tmp_path, monkeypatch):
    alerts: list = []
    monkeypatch.setattr(
        "index_ai.notify.send",
        lambda text, *, key=None, window_s=600.0: alerts.append((text, key)),
    )
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    journal.save_state({})

    class _Client:
        def positions(self):
            raise executor.DeltaError("boom", status=504)

    issues = executor.reconcile(_Client())
    assert any(i.startswith(executor.RECONCILE_UNREADABLE) for i in issues)
    assert alerts == []
