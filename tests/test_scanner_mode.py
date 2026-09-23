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


def test_no_option_reason_echoes_the_real_buy_sell_reasons() -> None:
    """The generic 'No option selected.' plan reason read like an error with
    no context — it should surface the actual buy/sell reasons instead."""
    from index_ai.scanner import _explain_no_option

    reason = _explain_no_option(
        "NO_TRADE",
        "No option selected.",
        {"reason": "No candlestick pattern at support/resistance this bar."},
        {"reason": "AUTO: bear-call credit blocked — the day is rallying."},
    )
    assert reason == (
        "buy: No candlestick pattern at support/resistance this bar. · "
        "sell: AUTO: bear-call credit blocked — the day is rallying."
    )

    # a real (non-generic) plan reason is left alone
    assert _explain_no_option("NO_TRADE", "Kill switch active.", {}, {}) == "Kill switch active."
    # a trade that *was* allowed is left alone even if the reason string matches
    assert _explain_no_option("BUY_CALL", "No option selected.", {}, {}) == "No option selected."
    # no buy/sell reason available at all -> keep the generic string, not ""
    assert _explain_no_option("NO_TRADE", "No option selected.", {}, {}) == "No option selected."


def test_paper_lane_event_dict_does_not_collide_with_log() -> None:
    """A lane's event dict carries its own "event" key; splatting it into
    _log(event, **fields) used to raise TypeError and kill the whole stage."""
    from index_ai.scanner import _log, _without_event

    payload = {"event": "entry", "instrument": "NIFTY", "strike": 24000}
    assert _without_event(payload) == {"instrument": "NIFTY", "strike": 24000}
    # the call that used to blow up
    _log("futures_paper", kind=payload.get("event"), **_without_event(payload))


def test_scan_index_runs_plan_off_the_event_loop(monkeypatch) -> None:
    """plan_instrument makes blocking Dhan HTTP calls; running it on the loop
    froze every dashboard poll for the length of each index scan."""
    import asyncio
    import time

    from index_ai import scanner

    def slow_plan(**_kw):
        time.sleep(0.3)  # stands in for the Dhan HTTP calls
        return {"error": "stub"}

    monkeypatch.setattr(scanner, "plan_instrument", slow_plan)
    ticks: list[float] = []

    async def heartbeat() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    async def main() -> None:
        beat = asyncio.create_task(heartbeat())
        await scanner._scan_index(None, None, "NIFTY")
        beat.cancel()

    asyncio.run(main())
    assert len(ticks) >= 5  # the loop kept ticking during the 0.3 s scan
