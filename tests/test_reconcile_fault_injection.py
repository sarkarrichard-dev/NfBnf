"""ORD-02 fault-injection tests (plan 02-05): a Dhan disconnect during the
live sync or reconciliation must never be read as "Dhan shows nothing" — no
false reject, no false close, no duplicate row, and every real problem still
reaches Telegram in plain words. Drives the real DhanClient/dhan_orders/
reconcile code against replayed real Dhan traffic (tests/_fake_brokers.py,
D-03) with injected transport faults, never a hand-rolled stub of the client.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import pytest

from _fake_brokers import BrokerReplay, fake_dhan_client, load_traffic
from index_ai.reconcile import GHOST_JOURNAL, ORPHAN_BROKER, OVER_FILLED, UNDER_FILLED

pytestmark = pytest.mark.filterwarnings("ignore")


# ── shared helpers ───────────────────────────────────────────────────────────


def _single_leg_live_option(**overrides) -> dict:
    base = dict(
        instrument="NIFTY",
        security_id=54321,
        segment="NSE_FNO",
        transaction_type="BUY",
        option_type="CALL",
        strike=25000,
        quantity=65,
        entry_ltp=100.0,
        broker_order_ids=["321"],
        broker_orders={"order_ids": ["321"]},
    )
    base.update(overrides)
    return base


def _record_live_traded_trade(
    option: dict, *, instrument: str = "NIFTY", created_at: str | None = None
) -> str:
    from index_ai.learning import connect, record_trade

    trade_id = record_trade(
        mode="LIVE",
        instrument=instrument,
        action="BUY_CALL",
        confidence=0.7,
        option=option,
        signal={"action": "BUY_CALL", "confidence": 0.7, "price": 24000},
        status="LIVE_TRADED",
    )
    if created_at is not None:
        with connect() as db:
            db.execute("UPDATE trades SET created_at = ? WHERE id = ?", (created_at, trade_id))
    return trade_id


def _fetch_trade_row(trade_id: str) -> dict | None:
    from index_ai.learning import _row_to_trade, connect

    with connect() as db:
        row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _row_to_trade(row) if row else None


def _row_count() -> int:
    from index_ai.learning import connect

    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]


def _fault_get_orders_disconnected(replay: BrokerReplay) -> None:
    """Exhaust all 4 GET-retry attempts (index_ai.dhan._MAX_RETRIES) so
    build_order_book_index's single client.list_today_orders() call genuinely
    fails end to end, rather than falling through to a scripted/recorded row
    after only a partial fault queue."""
    replay.fault(
        "GET",
        "/v2/orders",
        httpx.ConnectError,
        httpx.ConnectError,
        httpx.ConnectError,
        httpx.ConnectError,
    )


def _script_consistent_book(
    replay: BrokerReplay, *, order_id: str = "321", sid: int = 54321
) -> None:
    replay.script(
        "GET",
        "/v2/orders",
        [{"orderId": order_id, "orderStatus": "TRADED", "filledQty": 65, "quantity": 65}],
    )
    replay.script(
        "GET",
        "/v2/trades",
        [{"orderId": order_id, "tradedQuantity": 65, "tradedPrice": 100.0}],
    )
    replay.script("GET", "/v2/positions", [{"securityId": sid, "netQty": 65}])


# ── Task 1: the live sync survives a disconnect without touching a row ──────


def test_disconnect_young_trade_unchanged_one_alert(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option)
    before = _fetch_trade_row(trade_id)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    _fault_get_orders_disconnected(replay)
    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))
    client = fake_dhan_client(replay, monkeypatch)

    updated = sync_open_live_trades(client)

    assert updated == 0
    after = _fetch_trade_row(trade_id)
    assert after["status"] == before["status"]
    assert after["pnl"] == before["pnl"]
    assert after["option"] == before["option"]
    assert replay.count("POST", "/v2/orders") == 0
    assert len(alerts) == 1
    assert "1 open live trade" in alerts[0]


def test_disconnect_aged_trade_unchanged_no_exit(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option, created_at="2020-01-01T00:00:00+05:30")
    before = _fetch_trade_row(trade_id)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    _fault_get_orders_disconnected(replay)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    client = fake_dhan_client(replay, monkeypatch)

    updated = sync_open_live_trades(client)

    assert updated == 0
    after = _fetch_trade_row(trade_id)
    assert after["status"] == before["status"] == "LIVE_TRADED"
    assert after["pnl"] is None
    assert replay.count("POST", "/v2/orders") == 0
    assert replay.count("DELETE", "/v2/orders") == 0


def test_reconnect_consistent_stays_live_traded_no_alert(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    _script_consistent_book(replay)
    alerts: list[str] = []
    monkeypatch.setattr("index_ai.notify.send", lambda text, **k: alerts.append(text))
    client = fake_dhan_client(replay, monkeypatch)

    sync_open_live_trades(client)

    after = _fetch_trade_row(trade_id)
    assert after["status"] == "LIVE_TRADED"
    assert not alerts


def test_reconnect_position_genuinely_gone_closes_once(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option, created_at="2020-01-01T00:00:00+05:30")

    replay = BrokerReplay(load_traffic("dhan_rest"))
    replay.script(
        "GET",
        "/v2/orders",
        [{"orderId": "321", "orderStatus": "TRADED", "filledQty": 65, "quantity": 65}],
    )
    replay.script(
        "GET", "/v2/trades", [{"orderId": "321", "tradedQuantity": 65, "tradedPrice": 100.0}]
    )
    replay.script("GET", "/v2/positions", [])  # position genuinely gone
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    monkeypatch.setattr("index_ai.exit.option_ltp_with_retry", lambda *a, **k: 10.0)
    client = fake_dhan_client(replay, monkeypatch)

    updated = sync_open_live_trades(client)
    assert updated == 1
    after = _fetch_trade_row(trade_id)
    assert after["status"] == "CLOSED"
    assert after["pnl"] is not None

    # a second sync writes nothing more -- the row no longer matches
    # live_trades_for_broker_sync's "still open" query, so no broker call
    # happens at all.
    replay.calls.clear()
    updated_again = sync_open_live_trades(client)
    assert updated_again == 0
    assert replay.calls == []


def test_sync_never_inserts_or_loses_a_row(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    option = _single_leg_live_option()
    _record_live_traded_trade(option)
    before_count = _row_count()

    replay = BrokerReplay(load_traffic("dhan_rest"))
    _fault_get_orders_disconnected(replay)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    client = fake_dhan_client(replay, monkeypatch)

    sync_open_live_trades(client)  # disconnect
    assert _row_count() == before_count

    _script_consistent_book(replay)
    sync_open_live_trades(client)  # reconnect
    assert _row_count() == before_count
    sync_open_live_trades(client)  # reconnect again
    assert _row_count() == before_count


def test_no_live_trades_makes_no_broker_call() -> None:
    from index_ai.dhan_orders import sync_open_live_trades

    replay = BrokerReplay(load_traffic("dhan_rest"))
    client = fake_dhan_client(replay)

    assert sync_open_live_trades(client) == 0
    assert replay.calls == []


def test_sync_waits_for_instrument_lock(monkeypatch) -> None:
    from index_ai.dhan_orders import sync_open_live_trades
    from index_ai.execution_safety import acquire_execution_lock

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option)
    before = _fetch_trade_row(trade_id)

    replay = BrokerReplay(load_traffic("dhan_rest"))
    _script_consistent_book(replay)
    monkeypatch.setattr("index_ai.notify.send", lambda *a, **k: None)
    client = fake_dhan_client(replay, monkeypatch)

    lock = acquire_execution_lock("NIFTY")
    lock.acquire()
    result: dict = {}

    def run_sync() -> None:
        result["updated"] = sync_open_live_trades(client)

    t = threading.Thread(target=run_sync)
    t.start()
    try:
        time.sleep(0.3)
        assert t.is_alive(), "sync must still be blocked on the held instrument lock"
        during = _fetch_trade_row(trade_id)
        assert during["status"] == before["status"]
        assert during["pnl"] == before["pnl"]
    finally:
        lock.release()
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert "updated" in result


# ── Task 2: reconciliation reads strictly, catches untracked positions, ─────
# ── and tells Richard in plain words ─────────────────────────────────────────


class _FakePositionsClient:
    """Reconcile only ever calls ``list_positions()`` on its client -- a real
    DhanClient/BrokerReplay is unnecessary machinery for testing reconcile.py
    itself (Task 1's sync tests already exercise the real client end to end)."""

    def __init__(
        self, positions: list[dict] | None = None, *, raise_exc: BaseException | None = None
    ) -> None:
        self._positions = positions or []
        self._raise_exc = raise_exc

    def list_positions(self) -> list[dict]:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._positions


def test_reconcile_disconnect_with_open_row_blocks_and_alerts(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    option = _single_leg_live_option()
    trade_id = _record_live_traded_trade(option)
    monkeypatch.setenv("RECONCILE_AUTO_REPAIR", "true")
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    client = _FakePositionsClient(raise_exc=RuntimeError("boom"))

    out = reconcile(client, mode="LIVE")

    assert out["ok"] is False
    assert out["error"].startswith("could not read broker positions")
    assert out["issues"] == []
    assert out["repaired"] == []
    assert len(alerts) == 1
    assert alerts[0][1] == "reconcile:unreadable"

    row = _fetch_trade_row(trade_id)
    assert row["status"] == "LIVE_TRADED"
    assert row["pnl"] is None


def test_reconcile_disconnect_no_open_rows_is_quiet(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    client = _FakePositionsClient(raise_exc=RuntimeError("boom"))

    out = reconcile(client, mode="LIVE")

    assert out["ok"] is False
    assert out["error"].startswith("could not read broker positions")
    assert alerts == []


def test_reconcile_ghost_journal_alerts_with_leg_label_and_repairs(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    option = _single_leg_live_option(security_id=54321, strike=25000, option_type="CALL")
    trade_id = _record_live_traded_trade(option, instrument="NIFTY")
    monkeypatch.setenv("RECONCILE_AUTO_REPAIR", "true")
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    client = _FakePositionsClient(positions=[])  # Dhan holds nothing

    out = reconcile(client, mode="LIVE")

    assert out["ok"] is False
    assert len(out["issues"]) == 1
    assert out["issues"][0]["kind"] == GHOST_JOURNAL
    assert len(alerts) == 1
    text, key = alerts[0]
    assert key == f"reconcile:54321:{GHOST_JOURNAL}"
    assert "journal shows it open but Dhan holds nothing" in text
    assert "NIFTY" in text and "25000" in text and "CALL" in text

    assert out["repaired"] and out["repaired"][0]["trade_id"] == trade_id
    row = _fetch_trade_row(trade_id)
    assert row["status"] == "CLOSED_RECONCILED"


def test_reconcile_orphan_with_no_open_rows_alerts(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    client = _FakePositionsClient(positions=[{"securityId": 999, "netQty": 65}])

    out = reconcile(client, mode="LIVE")

    assert "skipped" not in out
    assert len(out["issues"]) == 1
    assert out["issues"][0]["kind"] == ORPHAN_BROKER
    assert len(alerts) == 1
    text, key = alerts[0]
    assert key == f"reconcile:999:{ORPHAN_BROKER}"
    assert "Dhan holds a position the system is not tracking" in text
    assert "999" in text and "65" in text


def test_reconcile_under_and_over_filled_each_alert(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    under_option = _single_leg_live_option(security_id=1001, quantity=65, transaction_type="BUY")
    over_option = _single_leg_live_option(security_id=1002, quantity=65, transaction_type="BUY")
    _record_live_traded_trade(under_option, instrument="NIFTY")
    _record_live_traded_trade(over_option, instrument="NIFTY")

    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "index_ai.notify.alert", lambda text, *, key, **k: alerts.append((text, key))
    )
    client = _FakePositionsClient(
        positions=[
            {"securityId": 1001, "netQty": 30},  # under-filled
            {"securityId": 1002, "netQty": 130},  # over-filled
        ]
    )

    out = reconcile(client, mode="LIVE")

    kinds = {i["security_id"]: i["kind"] for i in out["issues"]}
    assert kinds[1001] == UNDER_FILLED
    assert kinds[1002] == OVER_FILLED
    assert len(alerts) == 2
    texts = {key: text for text, key in alerts}
    assert "Dhan holds fewer than the journal expects" in texts[f"reconcile:1001:{UNDER_FILLED}"]
    assert "Dhan holds more than the journal expects" in texts[f"reconcile:1002:{OVER_FILLED}"]


def test_reconcile_alert_key_stable_across_repeated_calls(monkeypatch) -> None:
    """Same deterministic key on every call -- notify.alert's own hourly
    window (tested separately in test_notify.py) is what actually dedupes a
    second reconcile inside the hour; this proves reconcile.py feeds it a
    stable key so that dedup applies."""
    from index_ai.reconcile import reconcile

    option = _single_leg_live_option(security_id=54321)
    _record_live_traded_trade(option)
    keys: list[str] = []
    monkeypatch.setattr("index_ai.notify.alert", lambda text, *, key, **k: keys.append(key))
    client = _FakePositionsClient(positions=[])

    reconcile(client, mode="LIVE")
    reconcile(client, mode="LIVE")

    assert keys[0] == keys[1] == f"reconcile:54321:{GHOST_JOURNAL}"


def test_reconcile_telegram_failure_is_swallowed(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    option = _single_leg_live_option(security_id=54321)
    trade_id = _record_live_traded_trade(option)

    def _boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("index_ai.notify.alert", _boom)
    client = _FakePositionsClient(positions=[])

    out = reconcile(client, mode="LIVE")  # must not raise

    assert out["ok"] is False
    assert out["issues"][0]["kind"] == GHOST_JOURNAL
    row = _fetch_trade_row(trade_id)
    assert row is not None  # reconcile ran to completion despite the alert failure


def test_reconcile_paper_mode_no_broker_call_no_alert(monkeypatch) -> None:
    from index_ai.reconcile import reconcile

    alerts: list[Any] = []
    monkeypatch.setattr("index_ai.notify.alert", lambda *a, **k: alerts.append(a))

    class _BoomClient:
        def list_positions(self) -> list[dict]:
            raise AssertionError("must not be called in PAPER mode")

    out = reconcile(_BoomClient(), mode="PAPER")
    assert out["ok"] is True
    assert out["skipped"]
    assert alerts == []
