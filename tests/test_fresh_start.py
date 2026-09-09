"""fresh_start safety guards + the data-epoch filter."""

from __future__ import annotations

import pytest

from index_ai import strategy_performance as sp
from scripts import fresh_start as fs


def test_after_epoch_orders_iso_timestamps():
    ep = "2026-09-10T02:00:00+05:30"
    assert sp._after_epoch("2026-09-10T09:15:00", ep) is True
    assert sp._after_epoch("2026-09-09T23:59:00", ep) is False
    assert sp._after_epoch("2026-09-11", ep) is True  # date-only, later day
    assert sp._after_epoch("2026-09-08", ep) is False  # date-only, earlier day
    assert sp._after_epoch("2026-09-10T09:00:00", None) is True  # no epoch → keep all
    assert sp._after_epoch(None, ep) is True  # no ts → fail open


def test_epoch_filter_drops_pre_epoch_rows(monkeypatch):
    trades = [
        {
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 100.0,
            "created_at": "2026-09-01T10:00:00",
            "signal": {"strategy_mode": "x"},
            "option": {},
        },
        {
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "pnl": 200.0,
            "created_at": "2026-09-20T10:00:00",
            "signal": {"strategy_mode": "x"},
            "option": {},
        },
    ]
    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: trades)
    monkeypatch.setattr(sp, "data_epoch", lambda: "2026-09-10T00:00:00+05:30")

    rows = sp._india_rows()
    assert len(rows) == 1 and rows[0]["trades"] == 1 and rows[0]["gross"] == 200.0


def test_fresh_start_aborts_when_a_safety_probe_raises(monkeypatch, capsys):
    monkeypatch.setattr(fs, "_server_running", lambda: False)

    def boom():
        raise fs._SafetyProbeFailed("db is locked")

    monkeypatch.setattr(fs, "_live_positions_open", boom)
    rc = fs.main(["--commit"])
    assert rc == 1
    assert "could not run" in capsys.readouterr().out.lower()


def test_fresh_start_aborts_when_server_running(monkeypatch, capsys):
    monkeypatch.setattr(fs, "_server_running", lambda: True)
    rc = fs.main(["--commit"])
    assert rc == 1
    assert "server is running" in capsys.readouterr().out.lower()


def test_live_probe_flags_a_live_sent_row(monkeypatch):
    import index_ai.learning as learning

    monkeypatch.setattr(
        learning,
        "open_trades",
        lambda: [
            {
                "instrument": "NIFTY",
                "action": "SELL_BULL_PUT_SPREAD",
                "mode": "LIVE",
                "status": "LIVE_SENT",
            }
        ],
    )
    monkeypatch.setattr("crypto.day_review.open_positions", lambda: [])
    assert fs._live_positions_open() == ["index NIFTY SELL_BULL_PUT_SPREAD (LIVE_SENT)"]


def test_live_probe_raises_not_returns_empty_on_error(monkeypatch):
    import index_ai.learning as learning

    def boom():
        raise RuntimeError("locked")

    monkeypatch.setattr(learning, "open_trades", boom)
    with pytest.raises(fs._SafetyProbeFailed):
        fs._live_positions_open()
