from index_ai.config import settings
from index_ai.instruments import get_instrument
from index_ai.trailing import evaluate_open_trade, init_trail_meta


def _buy(entry=23400.0, action="BUY_CALL"):
    meta = init_trail_meta(entry_index_price=entry, action=action, transaction_type="BUY",
                           instrument=get_instrument("NIFTY"))
    return {"id": "t", "instrument": "NIFTY", "action": action,
            "signal": {"action": action, "price": entry},
            "option": {"transaction_type": "BUY", "ltp": 150.0, "last_option_ltp": 150.0,
                       "trail_meta": meta}}


def _step(trade, index_price):
    ev = evaluate_open_trade(trade, index_price, settings().risk)
    trade["option"]["trail_meta"] = ev["trail"]
    return ev


def test_nifty_call_stop_starts_25_below_and_follows_one_for_one():
    t = _buy()
    ev = _step(t, 23400)
    assert ev["trail"]["stop_index_price"] == 23375 and not ev["should_exit"]
    ev = _step(t, 23440)                                   # +40 in our favour
    assert ev["trail"]["stop_index_price"] == 23415
    ev = _step(t, 23420)                                   # pulls back, stop stays
    assert ev["trail"]["stop_index_price"] == 23415 and not ev["should_exit"]
    ev = _step(t, 23414)                                   # through the stop
    assert ev["should_exit"]


def test_put_mirrors_and_banknifty_uses_55():
    t = _buy(action="BUY_PUT")
    assert _step(t, 23400)["trail"]["stop_index_price"] == 23425
    assert get_instrument("BANKNIFTY").trail_distance_points == 55.0


def test_option_price_trail_no_longer_closes_buys_early():
    t = _buy()
    _step(t, 23400)
    # premium dips 12 pts (past the old 11-pt premium hard stop) while the index
    # is still above the 25-pt index stop -> the buy stays open
    t["option"]["last_option_ltp"] = 138.0
    assert not _step(t, 23390)["should_exit"]
