from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from index_ai.learning import (
    purge_test_learning_data,
    record_feedback,
    record_trade_outcome,
    update_learning,
)


def test_learning_ignores_unit_test_feedback() -> None:
    record_feedback("test-abc", -1, note="unit test loss")
    record_feedback("real-trade-id", 1, note="Manual win")
    learned = update_learning()
    assert learned["recent_feedback_count"] >= 1
    assert learned["recent_feedback_count"] >= 1


def test_purge_removes_test_rows() -> None:
    record_trade_outcome("test-purge-1", -100.0, note="unit test loss")
    removed = purge_test_learning_data()
    assert removed["feedback_removed"] >= 1


def test_schema_init_survives_concurrent_first_connect() -> None:
    """Regression: executor.py's entry path and scanner.py's exit path now
    both call into learning.connect() from separate threads (asyncio.to_thread).
    Before the _schema_lock fix, two threads racing the unlocked
    `if not _schema_initialized: init_db()` check-then-act reproducibly hit
    'sqlite3.OperationalError: database is locked' on a fresh DB."""
    import index_ai.learning as learning

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: record_trade_outcome(f"race-{i}", 1.0), range(20)))
    assert learning._schema_initialized is True
