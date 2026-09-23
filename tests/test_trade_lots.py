from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from index_ai.instruments import get_instrument
from index_ai.learning import resolve_trade_lot_size
from index_ai.server import app
from index_ai.trade_lots import (
    MAX_LOTS_PER_TRADE,
    MIN_LOTS_PER_TRADE,
    get_lots_per_trade,
    order_quantity,
    set_lots_per_trade,
    stamp_option_quantities,
)


@pytest.fixture
def lots_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "trade_lots.sqlite"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    from index_ai.learning import init_db

    init_db()
    set_lots_per_trade(1)


def test_set_and_clamp_lots(lots_db: None) -> None:
    set_lots_per_trade(3)
    assert get_lots_per_trade() == 3
    set_lots_per_trade(99)
    assert get_lots_per_trade() == MAX_LOTS_PER_TRADE
    set_lots_per_trade(0)
    assert get_lots_per_trade() == MIN_LOTS_PER_TRADE


def test_order_quantity_scales_with_lots(lots_db: None) -> None:
    set_lots_per_trade(2)
    inst = get_instrument("NIFTY")
    assert order_quantity(inst) == 130


def test_stamp_option_quantities_updates_legs(lots_db: None) -> None:
    set_lots_per_trade(2)
    inst = get_instrument("BANKNIFTY")
    option = stamp_option_quantities(
        {
            "quantity": 30,
            "legs": [
                {"transaction_type": "SELL", "quantity": 30},
                {"transaction_type": "BUY", "quantity": 30},
            ],
        },
        inst,
    )
    assert option["quantity"] == 60
    assert all(leg["quantity"] == 60 for leg in option["legs"])


def test_resolve_keeps_the_quantity_actually_traded(lots_db: None) -> None:
    # entered at 1 lot, then the dial moved to 2: the trade is still 1 lot
    set_lots_per_trade(2)
    trade = {"instrument": "NIFTY", "option": {"quantity": 65, "instrument": "NIFTY"}}
    configured, effective = resolve_trade_lot_size(trade)
    assert configured == 130
    assert effective == 65


def test_resolve_still_repairs_a_pre_revision_lot_quantity(lots_db: None) -> None:
    set_lots_per_trade(2)
    trade = {"instrument": "NIFTY", "option": {"quantity": 75, "instrument": "NIFTY"}}
    assert resolve_trade_lot_size(trade) == (130, 130)


def test_changing_lots_never_resizes_an_open_or_closed_trade(lots_db: None) -> None:
    """Regression: the lots change used to rewrite an open LIVE trade's quantity,
    so its exit order no longer matched the position held at the broker."""
    from index_ai.learning import open_trades, reconcile_all_trade_lots, record_trade

    record_trade(mode="LIVE", instrument="NIFTY", action="BUY_CE", confidence=0.7,
                 option={"quantity": 65, "instrument": "NIFTY", "security_id": 1},
                 signal={}, status="LIVE_OPEN")
    c = TestClient(app)
    c.post("/api/settings/lots", json={"lots": 3})
    reconcile_all_trade_lots()                     # the startup pass, all rows
    (t,) = open_trades()
    assert t["option"]["quantity"] == 65


def test_lots_api_adjust(lots_db: None) -> None:
    c = TestClient(app)
    base = c.get("/api/settings/lots").json()
    assert base["lots_per_trade"] == 1
    up = c.post("/api/settings/lots", json={"delta": 1}).json()
    assert up["lots_per_trade"] == 2
    assert up["policy"]["max_daily_loss_rupees"] == 18000
    down = c.post("/api/settings/lots", json={"delta": -1}).json()
    assert down["lots_per_trade"] == 1
    assert down["policy"]["max_daily_loss_rupees"] == 9000
