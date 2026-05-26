from __future__ import annotations

from index_ai.dhan import clamp_intraday_date_range


def test_clamp_intraday_range_to_five_calendar_days() -> None:
    from_d, to_d = clamp_intraday_date_range(
        "2026-05-01 09:15:00",
        "2026-05-26 15:30:00",
        max_calendar_days=5,
    )
    assert from_d.startswith("2026-05-21")
    assert to_d == "2026-05-26 15:30:00"
