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


def test_resolve_trade_lot_uses_configured_lots(lots_db: None) -> None:
    set_lots_per_trade(2)
    trade = {"instrument": "NIFTY", "option": {"quantity": 65, "instrument": "NIFTY"}}
    configured, effective = resolve_trade_lot_size(trade)
    assert configured == 130
    assert effective == 130


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
