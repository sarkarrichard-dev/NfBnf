from __future__ import annotations

import pandas as pd

from index_ai.strategies.bar_volume import volume_confirms


def test_volume_confirms_when_last_bar_above_average() -> None:
    frame = pd.DataFrame({"volume": [100] * 20 + [150]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert stats["ready"] is True
    assert ok is True
    assert stats["ratio"] >= 1.0


def test_volume_blocks_when_last_bar_too_low() -> None:
    frame = pd.DataFrame({"volume": [200] * 20 + [50]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert stats["ready"] is True
    assert ok is False
    assert stats["ratio"] < 0.65


def test_missing_volume_does_not_block() -> None:
    frame = pd.DataFrame({"close": [100.0, 101.0]})
    ok, stats = volume_confirms(frame, min_ratio=0.65)
    assert ok is True
    assert stats.get("ready") is False
