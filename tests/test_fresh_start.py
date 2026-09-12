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

    def boom(sections):
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
    assert fs._live_positions_open(["index"]) == ["index NIFTY SELL_BULL_PUT_SPREAD (LIVE_SENT)"]
    # a section not being reset isn't even probed
    assert fs._live_positions_open(["futures"]) == []


def test_live_probe_raises_not_returns_empty_on_error(monkeypatch):
    import index_ai.learning as learning

    def boom():
        raise RuntimeError("locked")

    monkeypatch.setattr(learning, "open_trades", boom)
    with pytest.raises(fs._SafetyProbeFailed):
        fs._live_positions_open(["index"])


def test_scoped_reset_touches_only_the_chosen_sections(tmp_path, monkeypatch):
    """Richard, 2026-09-12: reset crypto/futures/commodities without touching
    the already-reset index side. The index journal, its trained model, and
    another section's files must all survive a crypto-only reset untouched."""
    monkeypatch.setattr(fs, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(fs, "_server_running", lambda: False)
    monkeypatch.setattr(fs, "_live_positions_open", lambda sections: [])

    (tmp_path / "models").mkdir()
    index_files = ["trade_memory.sqlite", "models/brain_model.joblib", "day_review.json"]
    crypto_files = ["crypto_journal.jsonl", "crypto_state.json", "models/crypto_model.joblib"]
    futures_files = ["futures_journal.jsonl", "futures_paper.json"]
    commodity_files = ["commodity_journal.jsonl", "commodity_state.json"]
    kept_market_data = ["commodity_universe.json", "crypto_products.json"]
    for name in index_files + crypto_files + futures_files + commodity_files + kept_market_data:
        (tmp_path / name).write_text("x", encoding="utf-8")

    assert {p.name for p in fs._targets(["crypto"])} == {
        "crypto_journal.jsonl",
        "crypto_state.json",
        "crypto_model.joblib",
    }

    rc = fs.main(["--sections", "crypto,futures,commodities", "--commit"])
    assert rc == 0

    # the reset sections are gone from their live location, EXCEPT the
    # journal files, which get recreated empty (see the assertions below)
    reset_journals = {"crypto_journal.jsonl", "futures_journal.jsonl", "commodity_journal.jsonl"}
    for name in crypto_files + futures_files + commodity_files:
        if name in reset_journals:
            continue
        assert not (tmp_path / name).exists(), f"{name} should have been archived away"
    # ...but every index file, and the untouched market-data caches, survive
    for name in index_files + kept_market_data:
        assert (tmp_path / name).exists(), f"{name} should NOT have been touched"
    # the data epoch file must not exist -- this reset never called set_data_epoch()
    assert not (tmp_path / "data_epoch.txt").exists()
    # a fresh empty journal exists for each reset section, ready for the next trade
    assert (tmp_path / "crypto_journal.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "futures_journal.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "commodity_journal.jsonl").read_text(encoding="utf-8") == ""
    # everything really did land in the archive, nested under its real path
    archived = list((tmp_path / "archive").rglob("*"))
    assert any(p.name == "crypto_model.joblib" and p.parent.name == "models" for p in archived)
