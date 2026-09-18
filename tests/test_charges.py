from index_ai.charges import (
    ChargeRates,
    estimate_trade_cost,
    half_spread_points,
    leg_charge_rupees,
    round_trip_charge_breakdown,
    round_trip_charges_rupees,
)


def test_sell_leg_carries_stt_buy_leg_carries_stamp():
    r = ChargeRates()
    sell = leg_charge_rupees(200.0, 30, "SELL", rates=r)
    buy = leg_charge_rupees(200.0, 30, "BUY", rates=r)
    # turnover 6000: STT sell = 6, stamp buy = 0.18 -> sell strictly costlier
    assert sell > buy
    # F&O options: flat brokerage per order (not min with a percentage).
    assert sell == round(
        r.brokerage_per_order_rupees
        + r.exch_txn_pct_nse * 6000
        + r.sebi_pct * 6000
        + r.stt_sell_pct * 6000
        + r.gst_pct
        * (r.brokerage_per_order_rupees + r.exch_txn_pct_nse * 6000 + r.sebi_pct * 6000),
        2,
    )


def test_zero_premium_is_free():
    assert leg_charge_rupees(0, 30, "SELL") == 0.0
    assert leg_charge_rupees(120, 0, "BUY") == 0.0


def test_iron_condor_round_trip_beats_single_leg():
    single = {"ltp": 150, "transaction_type": "BUY"}
    condor = {
        "legs": [
            {"ltp": 120, "transaction_type": "SELL"},
            {"ltp": 60, "transaction_type": "BUY"},
            {"ltp": 120, "transaction_type": "SELL"},
            {"ltp": 60, "transaction_type": "BUY"},
        ]
    }
    assert round_trip_charges_rupees(condor, 30) > round_trip_charges_rupees(single, 30)


def test_sensex_uses_bse_exchange_rate():
    legs = {"ltp": 200, "transaction_type": "SELL"}
    nse = round_trip_charges_rupees(legs, 20, exchange="NSE")
    bse = round_trip_charges_rupees(legs, 20, exchange="BSE")
    assert bse < nse  # BSE exch txn pct is lower in defaults


def test_estimate_trade_cost_adds_slippage():
    opt = {
        "legs": [{"ltp": 100, "transaction_type": "SELL"}, {"ltp": 40, "transaction_type": "BUY"}]
    }
    tc = estimate_trade_cost(opt, 30, "BANKNIFTY")
    assert tc.slippage_rupees == half_spread_points("BANKNIFTY") * 30 * 2 * 2
    assert tc.total_rupees == round(tc.charges_rupees + tc.slippage_rupees, 2)
    assert tc.legs == 2


def test_half_spread_env_override(monkeypatch):
    monkeypatch.setenv("SLIPPAGE_HALF_SPREAD_POINTS_NIFTY", "5.5")
    assert half_spread_points("NIFTY") == 5.5


def test_charge_breakdown_total_matches_the_single_number():
    """2026-09-18: journaling/fees-tracking upgrade -- the itemised breakdown
    must always foot to the same total round_trip_charges_rupees already
    returns, for every structure shape (single leg, condor, SENSEX)."""
    single = {"ltp": 150, "transaction_type": "BUY"}
    condor = {
        "legs": [
            {"ltp": 120, "transaction_type": "SELL"},
            {"ltp": 60, "transaction_type": "BUY"},
            {"ltp": 120, "transaction_type": "SELL"},
            {"ltp": 60, "transaction_type": "BUY"},
        ]
    }
    for option, exchange in ((single, "NSE"), (condor, "NSE"), (single, "BSE")):
        total = round_trip_charges_rupees(option, 30, exchange=exchange)
        breakdown = round_trip_charge_breakdown(option, 30, exchange=exchange)
        assert breakdown["total"] == total
        assert set(breakdown) == {"brokerage", "stt", "exch_txn", "sebi", "gst", "stamp", "total"}


def test_charge_breakdown_sell_leg_carries_stt_buy_leg_carries_stamp():
    condor = {
        "legs": [
            {"ltp": 120, "transaction_type": "SELL"},
            {"ltp": 60, "transaction_type": "BUY"},
        ]
    }
    b = round_trip_charge_breakdown(condor, 30)
    # each leg opens then closes (opposite side), so both stt and stamp appear
    assert b["stt"] > 0
    assert b["stamp"] > 0
