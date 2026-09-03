from __future__ import annotations

from index_ai.learning import open_trades_for_mode, record_trade
from index_ai.scanner import _has_open_trade


def test_paper_open_does_not_block_live_scanner() -> None:
    record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        confidence=0.58,
        option={"structure": "BEAR_CALL_SPREAD", "legs": [{"strike": 23650}]},
        signal={"action": "SELL_BEAR_CALL_SPREAD", "price": 23500},
        status="PAPER_RECORDED",
    )
    assert _has_open_trade("NIFTY", mode="PAPER") is True
    assert _has_open_trade("NIFTY", mode="LIVE") is False
    assert len(open_trades_for_mode("LIVE")) == 0
    assert len(open_trades_for_mode("PAPER")) >= 1


def test_paper_lane_event_dict_does_not_collide_with_log() -> None:
    """A lane's event dict carries its own "event" key; splatting it into
    _log(event, **fields) used to raise TypeError and kill the whole stage."""
    from index_ai.scanner import _log, _without_event

    payload = {"event": "entry", "instrument": "NIFTY", "strike": 24000}
    assert _without_event(payload) == {"instrument": "NIFTY", "strike": 24000}
    # the call that used to blow up
    _log("options_cpr_paper", kind=payload.get("event"), **_without_event(payload))
