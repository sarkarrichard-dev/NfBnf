from __future__ import annotations

from index_ai.trailing import check_supertrend_exit, init_trail_meta
from index_ai.instruments import get_instrument


def test_supertrend_flip_triggers_exit() -> None:
    inst = get_instrument("NIFTY")
    meta = init_trail_meta(
        entry_index_price=22500.0,
        action="BUY_CALL",
        transaction_type="BUY",
        instrument=inst,
        supertrend_direction=1,
        supertrend_stop=22400.0,
    )
    fresh = {"ready": True, "direction": -1, "stop": 22600.0}
    hit, reason = check_supertrend_exit(meta, 22550.0, fresh)
    assert hit is True
    assert reason and "flip" in reason.lower()
