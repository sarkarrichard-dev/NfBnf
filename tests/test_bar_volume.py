from __future__ import annotations

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms

# Frames end with a still-forming bar (last row) that analyze_bar_volume drops —
# the row before it is the last closed bar the gate actually judges.


def test_volume_confirms_when_last_closed_bar_above_average() -> None:
    frame = pd.DataFrame({"volume": [100] * 20 + [150] + [5]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert stats["ready"] is True
    assert ok is True
    assert stats["ratio"] >= 1.0
    assert stats["last_bar_volume"] == 150  # not the forming 5


def test_volume_blocks_when_last_closed_bar_too_low() -> None:
    frame = pd.DataFrame({"volume": [200] * 20 + [50] + [1]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert stats["ready"] is True
    assert ok is False
    assert stats["ratio"] < 0.65


def test_forming_bar_alone_is_not_enough_data() -> None:
    frame = pd.DataFrame({"volume": [100, 5]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert stats.get("ready") is False
    assert ok is True  # missing/thin data never blocks


def test_missing_volume_does_not_block() -> None:
    frame = pd.DataFrame({"close": [100.0, 101.0]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert ok is True
    assert stats.get("ready") is False
