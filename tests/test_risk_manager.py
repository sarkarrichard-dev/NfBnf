import pytest

from index_ai import risk_manager
from index_ai.instruments import get_instrument


@pytest.fixture
def day(monkeypatch):
    """Set today's live P&L: india in ₹, crypto in $ (x88)."""
    from index_ai.learning import init_db
    from index_ai.trade_lots import set_lots_per_trade

    init_db()
    set_lots_per_trade(3)                       # budget = ₹9,000 x 3 = ₹27,000
    monkeypatch.setenv("CRYPTO_USDINR", "88")

    def _set(india=0.0, crypto_usd=0.0):
        monkeypatch.setattr("index_ai.learning.today_live_realized_pnl", lambda: india)
        monkeypatch.setattr(risk_manager, "_crypto_live_usd_today", lambda: crypto_usd)
    return _set


def test_states_count_india_and_crypto_together(day):
    day(india=-5_000)
    assert risk_manager.account_day()["state"] == "NORMAL"
    day(india=-5_000, crypto_usd=-100)          # ₹5,000 + ₹8,800 = ₹13,800 >= half of 27k
    d = risk_manager.account_day()
    assert d["state"] == "TIGHTENED" and d["lost_rupees"] == 13_800
    day(india=-20_000, crypto_usd=-100)
    assert risk_manager.account_day()["state"] == "STOPPED"
    day(india=+10_000, crypto_usd=-100)         # a winning India day offsets crypto
    assert risk_manager.account_day()["lost_rupees"] == 0


def test_tightened_day_sizes_new_india_orders_to_one_lot(day):
    from index_ai.execution_safety import validate_quantities
    from index_ai.trade_lots import order_quantity, stamp_option_quantities

    nifty = get_instrument("NIFTY")
    day()
    assert order_quantity(nifty) == 3 * nifty.lot_size
    day(india=-14_000)
    assert order_quantity(nifty) == nifty.lot_size
    opt = stamp_option_quantities({"quantity": 0, "security_id": 1}, nifty)
    assert validate_quantities(opt, nifty).ok     # stamp and pre-order check agree


def test_stopped_day_trips_both_kill_switches(day):
    from crypto.executor import kill_switch
    from index_ai.config import settings
    from index_ai.risk import kill_switch_state

    day(india=-1_000, crypto_usd=-300)          # ₹1,000 + ₹26,400 = ₹27,400
    ks = kill_switch_state(settings().risk)
    assert ks["triggered"] and any("India + crypto" in r for r in ks["reasons"])
    tripped, why = kill_switch()
    assert tripped and "India + crypto" in why


def test_read_failure_never_changes_size(day, monkeypatch):
    def boom():
        raise RuntimeError("db locked")
    monkeypatch.setattr(risk_manager, "account_day", boom)
    assert risk_manager.india_lots(3) == 3
    assert risk_manager.stopped() == (False, "")


def test_size_suggestions_never_raise_size_on_their_own(monkeypatch):
    def row(strategy, trades, net, first="2026-09-10", last="2026-09-30", cur="INR"):
        return {"strategy": strategy, "instrument": "NIFTY", "trades": trades, "net": net,
                "currency": cur, "first_day": first, "last_day": last}
    monkeypatch.setattr(
        "index_ai.strategy_performance.strategy_scorecard",
        lambda: {"india": {"rows": [row("loser", 20, -900), row("winner", 40, 5_000),
                                    row("young", 5, 800), row("ok", 20, 300)]},
                 "crypto": {"rows": []}},
    )
    got = {r["strategy"]: r["suggestion"] for r in risk_manager.size_suggestions()}
    assert got == {"loser": "cut to smallest size", "winner": "eligible for more — your call",
                   "young": "collecting", "ok": "keep"}


def test_api(day):
    from fastapi.testclient import TestClient

    from index_ai.server import app

    day(india=-14_000)
    body = TestClient(app).get("/api/risk-manager").json()
    assert body["day"]["state"] == "TIGHTENED"
    assert isinstance(body["suggestions"], list)
