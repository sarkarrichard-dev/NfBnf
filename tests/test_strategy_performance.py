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
    assert set(sc) == {"generated_at", "india", "crypto", "note"}
    for side in ("india", "crypto"):
        t = sc[side]["totals"]
        assert t["net"] == round(t["gross"] - t["charges"] - t["slippage"], 2)
