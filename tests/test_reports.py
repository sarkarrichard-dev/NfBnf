from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from index_ai.reports import build_report, export_filename, report_to_csv, resolve_report_window
from index_ai.server import app

IST = ZoneInfo("Asia/Kolkata")


def test_resolve_report_window_today() -> None:
    when = datetime(2026, 5, 29, 15, 30, tzinfo=IST)
    start, end, label = resolve_report_window("today", when=when)
    assert label == "2026-05-29"
    assert start == datetime(2026, 5, 29, 0, 0, tzinfo=IST)
    assert end == datetime(2026, 5, 30, 0, 0, tzinfo=IST)


def test_resolve_report_window_custom() -> None:
    start, end, label = resolve_report_window(
        "custom",
        from_date="2026-05-01",
        to_date="2026-05-10",
        when=datetime(2026, 5, 29, tzinfo=IST),
    )
    assert label == "2026-05-01_to_2026-05-10"
    assert start == datetime(2026, 5, 1, 0, 0, tzinfo=IST)
    assert end == datetime(2026, 5, 11, 0, 0, tzinfo=IST)


def test_resolve_report_window_custom_requires_dates() -> None:
    with pytest.raises(ValueError, match="from and to"):
        resolve_report_window("custom")


def test_report_to_csv_has_summary_and_orders_header() -> None:
    report = build_report("all")
    csv_text = report_to_csv(report)
    assert "PnL summary" in csv_text
    assert "Orders" in csv_text
    assert "trade_id" in csv_text
    assert "Realized PnL" in csv_text


def test_export_filename_uses_period_label() -> None:
    report = build_report("today")
    name = export_filename(report)
    assert name.startswith("index_options_ai_")
    assert name.endswith(".csv")
    assert "2026" in name or "all_time" in report.get("period_label", "")


def test_reports_export_endpoint_returns_csv() -> None:
    client = TestClient(app)
    response = client.get("/api/reports/export", params={"period": "today"})
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "attachment" in response.headers.get("content-disposition", "").lower()
    assert "PnL summary" in response.text


def test_reports_export_custom_bad_range() -> None:
    client = TestClient(app)
    response = client.get(
        "/api/reports/export",
        params={"period": "custom", "from": "2026-06-01", "to": "2026-05-01"},
    )
    assert response.status_code == 400
