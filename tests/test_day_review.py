from __future__ import annotations

from index_ai import day_review as dr


def _rows() -> list[dict]:
    return [
        {
            "id": "a",
            "instrument": "NIFTY",
            "action": "SELL_BEAR_CALL_SPREAD",
            "structure": "BEAR_CALL_SPREAD",
            "pnl_rupees": 300.0,
            "is_open": False,
            "signal_reason": "Bearish CPR + EMA aligned",
            "exit_reason": "Trailing exit: premium rose 35 pts",
        },
        {
            "id": "b",
            "instrument": "BANKNIFTY",
            "action": "BUY_CALL",
            "structure": "BUY_CALL",
            "pnl_rupees": -2000.0,
            "is_open": False,
            "signal_reason": "candlestick at S/R",
            "exit_reason": "End-of-session square-off (IST)",
        },
        {
            "id": "c",
            "instrument": "NIFTY",
            "action": "SELL_BULL_PUT_SPREAD",
            "structure": "BULL_PUT_SPREAD",
            "pnl_rupees": -120.0,
            "is_open": False,
            "signal_reason": "Bullish CPR",
            "exit_reason": "End-of-session square-off (IST)",
        },
        {
            "id": "d",
            "instrument": "NIFTY",
            "action": "SELL_BEAR_CALL_SPREAD",
            "structure": "BEAR_CALL_SPREAD",
            "pnl_rupees": None,
            "is_open": True,
            "signal_reason": "Bearish CPR",
            "exit_reason": None,
        },
    ]


def test_summary_aggregates() -> None:
    s = dr._summary(_rows())
    assert s["closed"] == 3 and s["open"] == 1
    assert s["wins"] == 1 and s["losses"] == 2
    assert s["net_rupees"] == 300.0 - 2000.0 - 120.0
    assert s["by_lane"]["sell"]["trades"] == 2
    assert s["by_lane"]["buy"]["net_rupees"] == -2000.0
    assert s["best_trade"]["id"] == "a" and s["worst_trade"]["id"] == "b"
    assert s["how_trades_ended"]["EOD square-off"] == 2
    assert s["how_trades_ended"]["trailing stop"] == 1


def test_exit_bucketing() -> None:
    assert dr._bucket_exit("Trailing exit: premium rose 35 pts off best") == "trailing stop"
    assert dr._bucket_exit("Hard stop: premium moved 105 pts against entry") == "stop loss"
    assert dr._bucket_exit("End-of-session square-off (IST)") == "EOD square-off"
    assert dr._bucket_exit("Credit profit target hit") == "profit target"
    assert dr._bucket_exit("") == "unknown"


def test_timestamp_tail_stripped() -> None:
    note = "Trailing exit: premium rose 35 pts @ 31 Aug 2026, 3:14:19 PM IST"
    assert dr._TS_TAIL.sub("", note).strip() == "Trailing exit: premium rose 35 pts"


def test_local_review_flags_riding_to_eod() -> None:
    s = dr._summary(_rows())
    lr = dr._local_review(_rows(), s)
    assert "narrative" in lr
    assert any("square-off" in w for w in lr["went_wrong"])
    assert any("BANKNIFTY" in w for w in lr["went_wrong"])  # worst trade


def test_local_review_empty_day() -> None:
    lr = dr._local_review([], dr._summary([]))
    assert lr["narrative"] == "No trades closed today."
