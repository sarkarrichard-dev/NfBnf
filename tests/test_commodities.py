"""MCX commodity paper lane — session clock, charges, and the enter/exit
plumbing (P&L math, journal shape) with the signal forced."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from commodities import lanes
from commodities.instruments import BY_KEY
from commodities.session import (
    entries_open,
    in_mcx_session,
    past_squareoff,
    us_dst_active,
)
from index_ai.market_clock import IST


def test_session_window_and_dst():
    mon_noon = datetime(2026, 6, 1, 12, 0, tzinfo=IST)
    mon_2340 = datetime(2026, 6, 1, 23, 40, tzinfo=IST)
    dec_2340 = datetime(2026, 12, 1, 23, 40, tzinfo=IST)
    sat = datetime(2026, 6, 6, 12, 0, tzinfo=IST)

    assert us_dst_active(mon_noon) and not us_dst_active(dec_2340)
    assert in_mcx_session(True, mon_noon)
    assert in_mcx_session(True, mon_2340)  # < 23:55 DST close
    assert not in_mcx_session(True, dec_2340)  # >= 23:30 std close
    assert not in_mcx_session(False, sat)
    assert entries_open(True, mon_noon)
    assert not entries_open(True, mon_2340)  # inside the 25-min entry cutoff
    assert past_squareoff(True, datetime(2026, 6, 1, 23, 52, tzinfo=IST))


def test_charges_clear_a_normal_crude_move():
    from commodities.charges import round_trip_cost_rupees, slippage_rupees

    crude = BY_KEY["CRUDEOILM"]
    cost = round_trip_cost_rupees(6000.0, 6060.0, crude, 1) + slippage_rupees(crude, 1)
    gross = (6060.0 - 6000.0) * crude.multiplier  # 60 pts * 10 = Rs 600
    assert 50 < cost < 130
    assert gross - cost > 400  # cost is a small fraction of the move


def test_lane_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_COMMODITIES_PAPER", "false")
    assert lanes.scan_commodities_paper() == []
    assert lanes.enabled() is False


def _two_day_frame(interval: str) -> pd.DataFrame:
    """Two IST calendar days of MCX 5m/15m bars, gently rising."""
    step = 5 if interval == "5" else 15
    n = 400 if interval == "5" else 140
    idx = pd.date_range("2026-06-01 09:00", periods=n, freq=f"{step}min", tz="Asia/Kolkata")
    idx = idx.tz_localize(None)
    c = 6000.0 + np.linspace(0, 40, n)
    return pd.DataFrame(
        {"datetime": idx, "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": [50.0] * n}
    )


def test_lane_plumbing_open_then_close(tmp_path, monkeypatch):
    """Force the engine to say LONG, then flip it — verify the commodities-side
    wiring: session gates, MCX P&L math, journal shape."""
    from index_ai.strategies.futures.engine import FLAT, LONG, SHORT, TrendRead

    monkeypatch.setenv("ENABLE_COMMODITIES_PAPER", "true")
    monkeypatch.setenv("COMMODITY_SYMBOLS", "CRUDEOILM")
    monkeypatch.setattr(lanes, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(lanes, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(lanes, "load_universe_meta", lambda: {"CRUDEOILM": {"security_id": 1}})
    monkeypatch.setattr(lanes, "entries_open", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "past_squareoff", lambda *a, **k: False)
    monkeypatch.setattr(lanes, "DhanClient", lambda *a, **k: object())

    frames = {"5": _two_day_frame("5"), "15": _two_day_frame("15")}
    monkeypatch.setattr(lanes, "_fetch", lambda client, spec, sid, interval: frames[interval])

    read = {"dir": LONG}
    monkeypatch.setattr(
        lanes,
        "trend_read",
        lambda *a, **k: TrendRead(read["dir"], 6040.0, 1, 1, 1, 6000.0, "forced"),
    )
    monkeypatch.setattr(lanes, "entry_trigger", lambda *a, **k: (True, "forced"))
    lanes._frame_cache.clear()

    ev = lanes.scan_commodities_paper()[0]
    assert ev["event"] == "entry" and ev["position"]["dir"] == "LONG"

    read["dir"] = SHORT  # trend flips -> exit at market
    ev = lanes.scan_commodities_paper()[0]
    assert ev["event"] == "exit"
    row = ev["trade"]
    assert row["lane"] == "commodities" and row["direction"] == "LONG"
    assert row["exit_reason"] == "trend_flip"
    assert row["multiplier"] == BY_KEY["CRUDEOILM"].multiplier
    # gross = (exit - entry) * multiplier * lots ; friction applied
    assert row["gross_rupees"] == pytest.approx(
        (row["exit"] - row["entry"]) * row["multiplier"] * row["lots"], abs=1
    )
    assert row["net_rupees"] == pytest.approx(row["gross_rupees"] - row["friction_rupees"], abs=1)

    read["dir"] = FLAT  # flat -> no new entry
    assert (
        lanes.scan_commodities_paper()[0]["event"] == "none"
        or lanes.scan_commodities_paper()[0].get("reason") == "no trend"
    )
