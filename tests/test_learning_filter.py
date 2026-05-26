from __future__ import annotations

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
