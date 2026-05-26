from __future__ import annotations

from index_ai.instruments import get_instrument
from index_ai.trailing import init_trail_meta, update_trail


def test_no_early_exit_on_small_pullback_before_activation() -> None:
    inst = get_instrument("NIFTY")
    meta = init_trail_meta(
        entry_index_price=24000.0,
        action="BUY_CALL",
        transaction_type="BUY",
        instrument=inst,
    )
    # Small adverse move — should NOT hit (initial stop is 100 pts)
    result = update_trail(meta, 23980.0)
    assert result["hit"] is False
    assert result["trail_armed"] is False


def test_trail_arms_after_activation_then_exits_on_distance() -> None:
    inst = get_instrument("NIFTY")
    meta = init_trail_meta(
        entry_index_price=24000.0,
        action="BUY_CALL",
        transaction_type="BUY",
        instrument=inst,
    )
    meta = update_trail(meta, 24030.0)  # +30 > activation 25
    assert meta["trail_armed"] is True
    meta = update_trail(meta, 24050.0)
    assert meta["anchor_index_price"] == 24050.0
    hit = update_trail(meta, 24009.0)  # 24050 - 40 = 24010
    assert hit["hit"] is True
