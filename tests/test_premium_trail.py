from __future__ import annotations

from index_ai.premium_trail import init_premium_trail, update_premium_trail


def _short_meta(entry: float, entry_index: float) -> dict:
    m = init_premium_trail(entry_premium=entry, direction=-1)
    m["entry_index_price"] = entry_index
    return m


def test_pivot_target_arms_trail_early_without_closing():
    m = _short_meta(300, 52000.0)
    # index short of the pivot — nothing happens, hard stop still governs
    m, ex, _ = update_premium_trail(m, 290, "BANKNIFTY", index_price=52150, pivot_target=52200)
    assert not ex and not m["pt_target_hit"]
    # index reaches the pivot — trail arms, trade keeps running
    m, ex, _ = update_premium_trail(m, 275, "BANKNIFTY", index_price=52210, pivot_target=52200)
    assert not ex and m["pt_target_hit"] and m["pt_armed_by"] == "pivot_target"


def test_no_pivot_target_is_unchanged_behaviour():
    m = _short_meta(300, 52000.0)
    # a 120-pt adverse move with no pivot target → still just the hard stop path
    m, ex, _ = update_premium_trail(m, 380, "BANKNIFTY", index_price=51800)
    assert not ex and not m["pt_target_hit"]
    m, ex, r = update_premium_trail(m, 405, "BANKNIFTY", index_price=51700)
    assert ex and "Hard stop" in r


def test_pivot_arm_direction_respects_bearish_trade():
    # short a call: favourable index move is DOWN, pivot target below entry
    m = _short_meta(400, 52000.0)
    m, ex, _ = update_premium_trail(m, 360, "BANKNIFTY", index_price=51900, pivot_target=51800)
    assert not m["pt_target_hit"]  # not there yet
    m, ex, _ = update_premium_trail(m, 340, "BANKNIFTY", index_price=51790, pivot_target=51800)
    assert m["pt_target_hit"]
