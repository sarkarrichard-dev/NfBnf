"""Per-(strategy, instrument) scorecard — synthetic journals in / rows out."""

from __future__ import annotations

import json

from index_ai import strategy_performance as sp


def test_india_rows_group_and_net(monkeypatch):
    trades = [
        {  # candlestick_buy NIFTY, a win — priced so charges apply
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 1000.0,
            "created_at": "2026-09-01T10:00:00",
            "signal": {"strategy_mode": "candlestick_buy"},
            "option": {
                "instrument": "NIFTY",
                "quantity": 75,
                "ltp": 100.0,
                "option_type": "CE",
                "strike": 24000,
                "transaction_type": "BUY",
            },
        },
        {  # candlestick_buy NIFTY, a loss
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": -400.0,
            "created_at": "2026-09-02T10:00:00",
            "signal": {"strategy_mode": "candlestick_buy"},
            "option": {
                "instrument": "NIFTY",
                "quantity": 75,
                "ltp": 80.0,
                "option_type": "CE",
                "strike": 24000,
                "transaction_type": "BUY",
            },
        },
        {  # different strategy + instrument
            "instrument": "BANKNIFTY",
            "action": "SELL_BULL_PUT_SPREAD",
            "mode": "PAPER",
            "pnl": -200.0,
            "created_at": "2026-09-02T11:00:00",
            "signal": {"strategy_mode": "cpr_trend"},
            "option": {"instrument": "BANKNIFTY", "quantity": 30},  # no ltp → unpriced
        },
        {
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": None,  # open, skipped
            "signal": {},
            "option": {},
        },
    ]
    monkeypatch.setattr(sp, "data_epoch", lambda: None)
    monkeypatch.setattr(sp, "recent_trades", lambda limit=0: trades, raising=False)
    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)

    rows = sp._india_rows()
    by = {(r["strategy"], r["instrument"]): r for r in rows}

    cb = by[("candlestick_buy", "NIFTY")]
    assert cb["trades"] == 2 and cb["wins"] == 1 and cb["losses"] == 1
    assert cb["gross"] == 600.0
    assert cb["charges"] > 0 and cb["net"] < cb["gross"]  # charges bite
    assert cb["priced_pct"] == 1.0

    cpr = by[("cpr_trend", "BANKNIFTY")]
    assert cpr["trades"] == 1 and cpr["gross"] == -200.0
    assert cpr["charges"] == 0.0 and cpr["priced_pct"] == 0.0  # couldn't cost it


def test_candlestick_buy_splits_by_pattern(monkeypatch):
    """2026-09-16: candlestick_buy lumped 6 different patterns into one bucket,
    hiding that breakout_resistance specifically was 0-for-2. Each pattern
    (entry_quality on the signal) must get its own row so a bad pattern can't
    hide behind a good one in the same average."""
    trades = [
        {
            "instrument": "BANKNIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": -3123.0,
            "created_at": "2026-09-16T13:00:00",
            "signal": {"strategy_mode": "candlestick_buy", "entry_quality": "breakout_resistance"},
            "option": {"instrument": "BANKNIFTY", "quantity": 30, "ltp": 552.05},
        },
        {
            "instrument": "SENSEX",
            "action": "BUY_PUT",
            "mode": "PAPER",
            "pnl": 2600.0,
            "created_at": "2026-09-15T14:12:00",
            "signal": {"strategy_mode": "candlestick_buy", "entry_quality": "breakdown_support"},
            "option": {"instrument": "SENSEX", "quantity": 20, "ltp": 595.25},
        },
    ]
    monkeypatch.setattr(sp, "data_epoch", lambda: None)
    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)

    rows = sp._india_rows()
    by = {(r["strategy"], r["instrument"]): r for r in rows}
    assert ("candlestick_buy · breakout_resistance", "BANKNIFTY") in by
    assert ("candlestick_buy · breakdown_support", "SENSEX") in by
    # not lumped into one plain "candlestick_buy" bucket
    assert not any(r["strategy"] == "candlestick_buy" for r in rows)


def test_crypto_rows_use_journal_fees(tmp_path, monkeypatch):
    j = tmp_path / "crypto_journal.jsonl"
    j.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "strategy": "ny_n_break",
                    "asset": "BTCUSD",
                    "mode": "paper",
                    "gross_usd": 5.0,
                    "fees_usd": 2.0,
                    "pnl_usd": 3.0,
                    "day": "2026-09-08",
                },
                {
                    "strategy": "ny_n_break",
                    "asset": "BTCUSD",
                    "mode": "paper",
                    "gross_usd": -1.0,
                    "fees_usd": 2.0,
                    "pnl_usd": -3.0,
                    "day": "2026-09-09",
                },
                {
                    "strategy": "ichimoku",
                    "asset": "ETHUSD",
                    "mode": "LIVE",
                    "gross_usd": 4.0,
                    "fees_usd": 1.0,
                    "pnl_usd": 3.0,
                    "day": "2026-09-09",
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("crypto.journal.JOURNAL_PATH", j)
    monkeypatch.setattr(sp, "data_epoch", lambda: None)

    rows = sp._crypto_rows()
    nb = next(r for r in rows if r["strategy"] == "ny_n_break")
    assert nb["trades"] == 2 and nb["gross"] == 4.0 and nb["charges"] == 4.0
    assert nb["net"] == 0.0  # 4 gross - 4 fees
    assert nb["currency"] == "USD"

    ic = next(r for r in rows if r["strategy"] == "ichimoku")
    assert ic["mode"] == "LIVE" and ic["net"] == 3.0


def test_scorecard_shape_against_real_journals():
    sc = sp.strategy_scorecard()
    assert set(sc) == {"generated_at", "india", "crypto", "commodities", "note"}
    for side in ("india", "crypto", "commodities"):
        t = sc[side]["totals"]
        assert t["net"] == round(t["gross"] - t["charges"] - t["slippage"], 2)


def test_strategy_scorecard_since_isolates_tuned_buy_entries(monkeypatch):
    """D-06/D-07: judge the tuned NIFTY buy entries on their own trades — the
    since cut-off must exclude the pre-switch trade, and the data epoch must
    still win when since is earlier than it."""
    trades = [
        {  # before the switch — must be excluded once since=2026-10-01
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 500.0,
            "created_at": "2026-09-20T10:00:00",
            "signal": {"strategy_mode": "candlestick_buy"},
            "option": {"instrument": "NIFTY", "quantity": 75, "ltp": 100.0},
        },
        {  # after the switch — always counted
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 700.0,
            "created_at": "2026-10-02T10:00:00",
            "signal": {"strategy_mode": "candlestick_buy"},
            "option": {"instrument": "NIFTY", "quantity": 75, "ltp": 100.0},
        },
    ]
    monkeypatch.setattr(sp, "data_epoch", lambda: "2026-09-10T00:00:00")
    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)

    both = sp.strategy_scorecard()["india"]["rows"]
    assert sum(r["trades"] for r in both) == 2

    since_next_switch = sp.strategy_scorecard(since="2026-10-01")["india"]["rows"]
    assert len(since_next_switch) == 1
    row = since_next_switch[0]
    assert row["trades"] == 1
    assert row["gross"] == 700.0

    # since earlier than the epoch -> the epoch (2026-09-10) still wins, so a
    # trade from 2026-09-05 (before the epoch) stays excluded
    early_trade = [
        {
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 100.0,
            "created_at": "2026-09-05T10:00:00",
            "signal": {"strategy_mode": "candlestick_buy"},
            "option": {"instrument": "NIFTY", "quantity": 75, "ltp": 100.0},
        }
    ]
    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: early_trade)
    excluded = sp.strategy_scorecard(since="2026-09-01")["india"]["rows"]
    assert sum(r["trades"] for r in excluded) == 0


def test_crypto_rows_unaffected_by_since(tmp_path, monkeypatch):
    j = tmp_path / "crypto_journal.jsonl"
    j.write_text(
        json.dumps(
            {
                "strategy": "cpr_trend",
                "asset": "BTCUSD",
                "mode": "paper",
                "gross_usd": 5.0,
                "fees_usd": 1.0,
                "pnl_usd": 4.0,
                "day": "2026-09-08",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("crypto.journal.JOURNAL_PATH", j)
    monkeypatch.setattr(sp, "data_epoch", lambda: None)
    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: [])

    no_since = sp.strategy_scorecard()["crypto"]
    with_since = sp.strategy_scorecard(since="2026-10-01")["crypto"]
    assert no_since == with_since
