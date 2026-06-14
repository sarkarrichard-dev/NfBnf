from __future__ import annotations

import pytest

from index_ai.learning import init_db, today_consecutive_loss_streak


@pytest.fixture
def loss_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "loss.sqlite"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    init_db()


def _insert_trade(db_path, trade_id: str, created_at: str, pnl: float | None) -> None:
    import sqlite3

    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO trades
            (id, created_at, mode, instrument, action, confidence, option_json, signal_json, status, pnl)
            VALUES (?, ?, 'PAPER', 'NIFTY', 'BUY_CALL', 0.7, '{}', '{}', 'CLOSED', ?)
            """,
            (trade_id, created_at, pnl),
        )


def test_consecutive_streak_resets_after_win(loss_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai.config import DB_PATH

    day = "2026-05-31"
    monkeypatch.setattr("index_ai.learning.today_ist_date", lambda: day)
    _insert_trade(DB_PATH, "a", f"{day}T10:00:00+05:30", -100.0)
    _insert_trade(DB_PATH, "b", f"{day}T11:00:00+05:30", -200.0)
    _insert_trade(DB_PATH, "c", f"{day}T12:00:00+05:30", 50.0)
    _insert_trade(DB_PATH, "d", f"{day}T13:00:00+05:30", -300.0)
    assert today_consecutive_loss_streak() == 1


def test_consecutive_streak_triggers_at_three(loss_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai.config import DB_PATH

    day = "2026-05-31"
    monkeypatch.setattr("index_ai.learning.today_ist_date", lambda: day)
    for i, pnl in enumerate([-100.0, -50.0, -25.0]):
        _insert_trade(DB_PATH, f"t{i}", f"{day}T{10 + i}:00:00+05:30", pnl)
    assert today_consecutive_loss_streak() == 3
