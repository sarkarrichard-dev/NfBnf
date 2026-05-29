from __future__ import annotations

from datetime import date

from index_ai.options_expiry import parse_expiry_date, pick_nearest_expiry


def test_parse_expiry_iso() -> None:
    assert parse_expiry_date("2026-05-30") == date(2026, 5, 30)


def test_pick_nearest_not_first_in_list() -> None:
  # Dhan may return monthly before weekly — pick closest on/after ref.
    expiries = ["2026-06-30", "2026-05-30", "2026-06-05"]
    picked = pick_nearest_expiry(expiries, now=__import__("datetime").datetime(2026, 5, 29, 10, 0))
    assert picked == "2026-05-30"


def test_pick_nearest_skips_past() -> None:
    expiries = ["2026-05-28", "2026-06-05", "2026-06-30"]
    picked = pick_nearest_expiry(expiries, now=__import__("datetime").datetime(2026, 5, 29, 10, 0))
    assert picked == "2026-06-05"
