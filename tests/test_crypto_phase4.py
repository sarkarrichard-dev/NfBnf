"""Phase 4 crypto — live-trading locks, the executor, and the live lane branch.

No network: the Delta client / executor are mocked. No .env writes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from crypto import executor, journal, lanes, live, notify
from crypto.config import crypto_settings
from crypto.delta.products import Contract


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------
def test_arm_phrase_is_distinct_and_exact(monkeypatch):
    writes: dict[str, str] = {}
    monkeypatch.setattr(live, "update_env_values", lambda v: writes.update(v))

    assert live.CRYPTO_ARM_PHRASE == "ARM CRYPTO LIVE" != "ARM LIVE ORDERS"
    with pytest.raises(ValueError):
        live.arm_crypto_live("ARM LIVE ORDERS")  # the index phrase must not work
    assert live.arm_crypto_live("ARM CRYPTO LIVE") and writes["CRYPTO_ALLOW_LIVE"] == "true"
    writes.clear()
    assert live.set_crypto_mode("PAPER") == "PAPER"
    assert writes["CRYPTO_ALLOW_LIVE"] == "false"  # PAPER always disarms


def test_live_orders_enabled_needs_all_three(monkeypatch):
    for k in ("CRYPTO_TRADING_MODE", "CRYPTO_ALLOW_LIVE", "DELTA_API_KEY", "DELTA_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRYPTO_TRADING_MODE", "LIVE")
    monkeypatch.setenv("CRYPTO_ALLOW_LIVE", "true")
    assert not crypto_settings().live_orders_enabled  # no credentials
    monkeypatch.setenv("DELTA_API_KEY", "k")
    monkeypatch.setenv("DELTA_API_SECRET", "s")
    assert crypto_settings().live_orders_enabled
    monkeypatch.setenv("CRYPTO_ALLOW_LIVE", "false")
    assert not crypto_settings().live_orders_enabled


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------
class _Client:
    def __init__(self, positions=None):
        self.calls: list = []
        self._positions = positions or []

    def signed(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, body))
        if path.endswith("/leverage"):
            return {}
        if path == "/v2/orders":
            return {"id": 777, "product_id": body["product_id"], "state": "closed"}
        if path == "/v2/fills":
            return [{"order_id": 777, "size": 2, "price": 61500.0}]
        return {}

    def wallet(self):
        return [{"balance": 500.0, "balance_inr": 44000.0}]

    def positions(self):
        return self._positions


BTC = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)


def test_place_entry_payload_and_leverage_first():
    c = _Client()
    r = executor.place_entry(c, BTC, "long", 2, leverage=3, sl_price=59000)
    assert r["order_id"] == "27:777"
    assert c.calls[0][1].endswith("/orders/leverage")  # leverage BEFORE the order
    body = c.calls[1][2]
    assert body["side"] == "buy" and body["order_type"] == "market_order"
    assert body["bracket_stop_loss_price"] == "59000.0"
    # no SL → no bracket field
    c2 = _Client()
    executor.place_entry(c2, BTC, "short", 1, leverage=5)
    assert "bracket_stop_loss_price" not in c2.calls[1][2]
    assert c2.calls[1][2]["side"] == "sell"


def test_place_exit_is_reduce_only_opposite_side():
    c = _Client()
    executor.place_exit(c, BTC, "long", 2)
    body = c.calls[-1][2]
    assert body["reduce_only"] is True and body["side"] == "sell"


def test_kill_switch_trips_on_daily_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "j.jsonl")
    today = datetime.now(timezone.utc).date().isoformat()
    for _ in range(2):
        journal.journal({"mode": "live", "closed_at": f"{today}T10:00:00", "pnl_usd": -30.0})

    class _S:
        max_daily_loss_usd = 50.0
        max_consec_losses = 9

    trip, why = executor.kill_switch(_S())
    assert trip and "daily loss" in why


def test_live_gate_disarms_on_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "j.jsonl")
    disarmed = {}
    monkeypatch.setattr(executor, "disarm_crypto_live", lambda: disarmed.setdefault("hit", True))
    monkeypatch.setattr("crypto.notify.alert", lambda *a, **k: None)
    today = datetime.now(timezone.utc).date().isoformat()
    for _ in range(3):
        journal.journal({"mode": "live", "closed_at": f"{today}T10:00:00", "pnl_usd": -5.0})

    class _S:
        live_orders_enabled = True
        max_daily_loss_usd = 999.0
        max_consec_losses = 3

    ok, why = executor.live_gate(_S())
    assert not ok and "kill switch" in why and disarmed.get("hit")


def test_reconcile_flags_mismatch_without_trading(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "s.json")
    journal.save_state(
        {"ny_n_break:BTCUSD": {"position": {"asset": "BTCUSD", "side": "long", "mode": "live"}}}
    )
    monkeypatch.setattr("crypto.notify.alert", lambda *a, **k: None)
    issues = executor.reconcile(_Client(positions=[]))  # Delta shows flat
    assert any("Delta shows FLAT" in i for i in issues)

    # a paper position is never a Delta mismatch
    journal.save_state(
        {"ny_n_break:ETHUSD": {"position": {"asset": "ETHUSD", "side": "long", "mode": "paper"}}}
    )
    assert executor.reconcile(_Client(positions=[])) == []


# ---------------------------------------------------------------------------
# lane — live branch
# ---------------------------------------------------------------------------
def _nbreak_frame() -> pd.DataFrame:
    base = (
        list(range(100, 131))
        + [131, 132, 133, 134, 135, 134, 133, 132]
        + [133, 131, 129, 128, 128, 129, 130, 132, 133, 134, 134, 133, 134, 136, 130]
    )
    closes = [c * 450.0 for c in base]
    return pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-09-07 18:00", periods=len(closes), freq="5min", tz="Asia/Kolkata"
            ),
            "open": closes,
            "high": [c + 450 for c in closes],
            "low": [c - 450 for c in closes],
            "close": closes,
            "volume": [10.0] * len(closes),
        }
    )


@pytest.fixture
def live_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_TRADING_MODE", "LIVE")
    monkeypatch.setenv("CRYPTO_ALLOW_LIVE", "true")
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSD,ETHUSD")
    monkeypatch.setenv("DELTA_API_KEY", "k")
    monkeypatch.setenv("DELTA_API_SECRET", "s")
    monkeypatch.setenv("ENABLE_CRYPTO_PAPER", "false")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_USDINR", "88")
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")

    contracts = {"BTCUSD": BTC, "ETHUSD": Contract("ETHUSD", 3136, 0.01, 0.05, 1, 100)}
    monkeypatch.setattr(lanes.products, "all_contracts", lambda client=None: contracts)
    monkeypatch.setattr(
        lanes.market_data,
        "candles",
        lambda sym, res, **k: (
            _nbreak_frame()
            if (sym == "BTCUSD" and res == "5m")
            else _nbreak_frame().iloc[::3].reset_index(drop=True)
            if sym == "BTCUSD"
            else _nbreak_frame().assign(close=200000.0, open=200000.0, high=201000.0, low=199000.0)
        ),
    )
    monkeypatch.setattr(lanes.market_data, "depth", lambda *a, **k: {})
    monkeypatch.setattr(lanes.charges, "sample_spread", lambda *a, **k: None)
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "ny_session_date", lambda *a, **k: "2026-09-07")
    monkeypatch.setattr(lanes, "_live_wallet_usd", lambda c: 5000.0)
    monkeypatch.setattr(notify, "opened", lambda p: None)
    monkeypatch.setattr(notify, "closed", lambda r: None)
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(lanes.executor, "reconcile", lambda c: [])
    monkeypatch.setattr(lanes.executor, "live_gate", lambda s=None: (True, ""))
    monkeypatch.setattr(lanes.executor, "position_state", lambda c, sym: "open")
    monkeypatch.setattr(lanes.executor, "fill_report", lambda c, oid: (None, 0.0))
    monkeypatch.setattr(lanes.executor, "fill_price", lambda c, oid: None)
    return monkeypatch


def test_live_entry_failure_records_no_position(live_lane):
    def boom(*a, **k):
        raise RuntimeError("delta 400: insufficient margin")

    live_lane.setattr(lanes.executor, "place_entry", boom)
    events = lanes.scan_crypto_paper()
    assert any(e.get("event") == "live_rejected" for e in events)
    assert not journal.load_state().get("ny_n_break:BTCUSD", {}).get("position")


def test_order_fills_but_fill_lookup_throws_still_records_position(live_lane):
    # the order IS live on Delta — a failed /v2/fills read must not drop it
    live_lane.setattr(lanes.executor, "place_entry", lambda *a, **k: {"order_id": "27:9"})

    def boom(*a, **k):
        raise ValueError("malformed fills row")

    live_lane.setattr(lanes.executor, "fill_report", boom)
    lanes.scan_crypto_paper()
    pos = journal.load_state()["ny_n_break:BTCUSD"]["position"]
    assert pos and pos["mode"] == "live" and pos["order_id"] == "27:9"
    assert pos["entry_price_source"] == "signal"  # fell back, did not lose the position


def test_position_closed_on_exchange_is_reaped(live_lane):
    live_lane.setattr(lanes.executor, "place_entry", lambda *a, **k: {"order_id": "27:1"})
    lanes.scan_crypto_paper()
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"]["mode"] == "live"

    # bracket stop filled — Delta now shows flat, strategy still says "hold"
    live_lane.setattr(lanes.executor, "position_state", lambda c, sym: "flat")
    live_lane.setattr(lanes.market_data, "ticker", lambda sym, **k: {"mark_price": 58000.0})
    events = lanes.scan_crypto_paper()
    assert any(e.get("event") == "reaped" for e in events)
    rows = journal.recent()
    assert len(rows) == 1 and rows[0]["exit_reason"] == "closed on exchange"
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None


def test_live_round_trip_journals_real_fill(live_lane):
    live_lane.setattr(lanes.executor, "place_entry", lambda *a, **k: {"order_id": "27:1"})
    live_lane.setattr(lanes.executor, "place_exit", lambda *a, **k: {"order_id": "27:2"})
    live_lane.setattr(lanes.executor, "fill_report", lambda c, oid: (61234.0, 3.0))
    live_lane.setattr(lanes.executor, "fill_price", lambda c, oid: 61999.0)

    lanes.scan_crypto_paper()  # opens live
    pos = journal.load_state()["ny_n_break:BTCUSD"]["position"]
    assert pos["mode"] == "live" and pos["entry_price"] == 61234.0 and pos["order_id"] == "27:1"
    assert pos["entry_price_source"] == "fill"

    live_lane.setattr(lanes, "in_ny_window", lambda *a, **k: False)  # session end -> exit
    lanes.scan_crypto_paper()
    rows = journal.recent()
    assert len(rows) == 1 and rows[0]["mode"] == "live"
    assert rows[0]["exit_price"] == 61999.0
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None


# ---------------------------------------------------------------------------
# /api/crypto/mode + /arm-live
# ---------------------------------------------------------------------------
def test_mode_and_arm_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from index_ai.server import app

    writes: dict[str, str] = {}
    monkeypatch.setattr("crypto.live.update_env_values", lambda v: writes.update(v))
    monkeypatch.setattr(
        "crypto.api._egress",
        lambda s: {
            "ipv4": "1.2.3.4",
            "ipv6": None,
            "forcing_ipv4": True,
            "delta_sees_ip": None,
            "whitelist_ok": True,
        },
    )
    c = TestClient(app)

    assert c.post("/api/crypto/mode", json={"mode": "LIVE"}).json()["trading_mode"] == "LIVE"
    assert writes["CRYPTO_TRADING_MODE"] == "LIVE"
    # wrong phrase is refused
    assert c.post("/api/crypto/arm-live", json={"confirm": "ARM LIVE ORDERS"}).status_code == 400
    r = c.post("/api/crypto/arm-live", json={"confirm": "ARM CRYPTO LIVE"}).json()
    assert writes["CRYPTO_ALLOW_LIVE"] == "true" and r["confirm_phrase"] == "ARM CRYPTO LIVE"
    # PAPER disarms
    c.post("/api/crypto/mode", json={"mode": "PAPER"})
    assert writes["CRYPTO_ALLOW_LIVE"] == "false"
    body = c.get("/api/crypto/status").json()
    assert "kill_switch" in body and body["egress"]["ipv4"] == "1.2.3.4"
    assert body["arm_phrase"] == "ARM CRYPTO LIVE"


def test_live_exit_failure_keeps_position_open(live_lane):
    live_lane.setattr(lanes.executor, "place_entry", lambda *a, **k: {"order_id": "27:1"})
    live_lane.setattr(lanes.executor, "fill_price", lambda c, oid: 61234.0)
    lanes.scan_crypto_paper()
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"]["mode"] == "live"

    def boom(*a, **k):
        raise RuntimeError("delta 500")

    live_lane.setattr(lanes.executor, "place_exit", boom)
    live_lane.setattr(lanes, "in_ny_window", lambda *a, **k: False)
    lanes.scan_crypto_paper()
    # exit order failed → still exposed, nothing journalled
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is not None
    assert journal.recent() == []
