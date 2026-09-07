from __future__ import annotations

from index_ai.premium_trail import init_premium_trail, update_premium_trail


def _short_meta(entry: float, entry_index: float) -> dict:
    m = init_premium_trail(entry_premium=entry, direction=-1)
    m["entry_index_price"] = entry_index
    return m


def test_pivot_target_arms_trail_only_when_already_in_profit():
    m = _short_meta(300, 52000.0)
    # index at the pivot but the short is only 12 pts in profit → not armed
    m, ex, _ = update_premium_trail(m, 288, "BANKNIFTY", index_price=52210, pivot_target=52200)
    assert not ex and not m["pt_target_hit"]
    # now 55 pts in profit and index past the pivot → trail arms, trade runs on
    m, ex, _ = update_premium_trail(m, 245, "BANKNIFTY", index_price=52210, pivot_target=52200)
    assert not ex and m["pt_target_hit"] and m["pt_armed_by"] == "pivot_target"


def test_pivot_arm_cannot_book_a_loss_on_a_whipsaw():
    m = _short_meta(300, 52000.0)
    m, _, _ = update_premium_trail(m, 250, "BANKNIFTY", index_price=52210, pivot_target=52200)
    assert m["pt_target_hit"]  # armed at +50
    # premium whips all the way back up — exit still locks a gain, not a loss
    m, ex, r = update_premium_trail(m, 288, "BANKNIFTY", index_price=52210, pivot_target=52200)
    assert ex and "Trailing exit" in r and "locked 12" in r


def test_bad_index_tick_never_arms():
    m = _short_meta(300, 52000.0)
    m, _, _ = update_premium_trail(m, 240, "BANKNIFTY", index_price=0.0, pivot_target=51800)
    assert not m["pt_target_hit"]
    m, _, _ = update_premium_trail(m, 240, "BANKNIFTY", index_price=999999.0, pivot_target=51800)
    assert not m["pt_target_hit"]


def test_no_pivot_target_is_unchanged_behaviour():
    m = _short_meta(300, 52000.0)
    m, ex, _ = update_premium_trail(m, 380, "BANKNIFTY", index_price=51800)
    assert not ex and not m["pt_target_hit"]
    m, ex, r = update_premium_trail(m, 405, "BANKNIFTY", index_price=51700)
    assert ex and "Hard stop" in r


def test_pivot_arm_direction_respects_bearish_trade():
    # short a call: favourable index move is DOWN, pivot target below entry
    m = _short_meta(400, 52000.0)
    m, _, _ = update_premium_trail(m, 350, "BANKNIFTY", index_price=51900, pivot_target=51800)
    assert not m["pt_target_hit"]  # index not yet at the pivot
    m, _, _ = update_premium_trail(m, 350, "BANKNIFTY", index_price=51790, pivot_target=51800)
    assert m["pt_target_hit"]
