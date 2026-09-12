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


def test_atr_scaled_spec_scales_the_stop_to_each_contracts_own_volatility(monkeypatch):
    """Richard, 2026-09-12: gold and silver don't move the same amount, so one
    flat stop-loss percentage for every commodity is wrong for at least some
    of them. A calmer measured instrument must come out tighter than a wilder
    one, using the same numbers actually measured off real MCX daily bars."""
    monkeypatch.setattr(lanes, "_atr_cache", {})  # isolate from other tests

    def fake_measure(spec, security_id, client):
        return {"GOLDM": 1.56, "CRUDEOILM": 3.98}.get(spec.key)

    monkeypatch.setattr(lanes, "_measure_daily_atr_pct", fake_measure)

    calm = lanes.atr_scaled_spec(BY_KEY["GOLDM"], 1, client=object())
    wild = lanes.atr_scaled_spec(BY_KEY["CRUDEOILM"], 2, client=object())
    assert calm.initial_stop_pct < wild.initial_stop_pct
    assert calm.initial_stop_pct == round(lanes.ATR_K_INITIAL_STOP * 1.56, 3)
    assert calm.trail_pct == round(lanes.ATR_K_TRAIL * 1.56, 3)
    # everything else on the spec (multiplier, tick, label...) is untouched
    assert calm.multiplier == BY_KEY["GOLDM"].multiplier and calm.label == BY_KEY["GOLDM"].label

    # a fresh call within the 24h TTL must not re-measure
    monkeypatch.setattr(
        lanes,
        "_measure_daily_atr_pct",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("shouldn't refetch")),
    )
    again = lanes.atr_scaled_spec(BY_KEY["GOLDM"], 1, client=object())
    assert again.initial_stop_pct == calm.initial_stop_pct

    # measurement never available -> the static spec, unchanged, not a crash
    monkeypatch.setattr(lanes, "_atr_cache", {})
    monkeypatch.setattr(lanes, "_measure_daily_atr_pct", lambda *a, **k: None)
    fallback = lanes.atr_scaled_spec(BY_KEY["SILVERMIC"], 3, client=object())
    assert fallback == BY_KEY["SILVERMIC"]


def test_tick_opens_a_position_using_the_atr_scaled_stop(monkeypatch):
    """The whole point: a live scan must actually use the measured stop, not
    silently keep the old flat guess."""
    from index_ai.strategies.futures.engine import LONG, TrendRead

    monkeypatch.setattr(lanes, "_atr_cache", {})
    monkeypatch.setattr(
        lanes, "_measure_daily_atr_pct", lambda spec, sid, client: 1.56
    )  # GOLDM-like
    monkeypatch.setattr(lanes, "entries_open", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "past_squareoff", lambda *a, **k: False)

    n = 400
    idx = pd.date_range("2026-06-01 09:00", periods=n, freq="5min")
    c = 6000.0 + np.linspace(0, 40, n)
    frame = pd.DataFrame(
        {"datetime": idx, "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": [50.0] * n}
    )
    monkeypatch.setattr(lanes, "_fetch", lambda client, spec, sid, interval: frame)
    monkeypatch.setattr(
        lanes, "trend_read", lambda *a, **k: TrendRead(LONG, c[-1] - 10, 1, 1, 1, c[0], "forced")
    )
    monkeypatch.setattr(lanes, "entry_trigger", lambda *a, **k: (True, "forced"))

    spec = BY_KEY["GOLDM"]  # static default is 0.45%, ATR-scaled should differ
    ev = lanes.tick(object(), spec, 1, lanes.commodity_settings(), {}, 0)
    assert ev["event"] == "entry"
    pos = ev["position"]
    expected_stop_pct = round(lanes.ATR_K_INITIAL_STOP * 1.56, 3)
    assert expected_stop_pct != spec.initial_stop_pct  # the fixture is a real change, not a no-op
    implied_pct = abs(pos["entry"] - pos["stop"]) / pos["entry"] * 100.0
    assert implied_pct == pytest.approx(expected_stop_pct, abs=0.01)


def test_status_reports_mtm_for_an_open_position(tmp_path, monkeypatch):
    """commodities_status() must attach a live mark + unrealized P&L to an open
    position — this is what the dashboard's MTM grid reads. Regression for the
    version that returned the raw position with no mark at all."""
    crude = BY_KEY["CRUDEOILM"]
    state = {
        "CRUDEOILM": {
            "position": {
                "instrument": "CRUDEOILM",
                "dir": "LONG",
                "entry": 6000.0,
                "entry_time": "2026-06-01T09:20:00+05:30",
                "day": "2026-06-01",
                "peak": 6000.0,
                "stop": 5949.0,
                "armed": False,
                "mode": "PAPER",
            }
        }
    }
    monkeypatch.setattr(lanes, "_load_state", lambda: state)
    monkeypatch.setattr(lanes, "_recent", lambda n: [])
    monkeypatch.setattr(lanes, "load_universe_meta", lambda: {"CRUDEOILM": {"security_id": 1}})
    monkeypatch.setattr(lanes, "DhanClient", lambda *a, **k: object())
    monkeypatch.setattr(
        lanes, "_fetch", lambda client, spec, sid, interval: pd.DataFrame({"close": [6060.0]})
    )

    status = lanes.commodities_status()
    pos = status["open_positions"]["CRUDEOILM"]
    assert pos["mark"] == 6060.0
    expected_pnl = (6060.0 - 6000.0) * crude.multiplier * status["lots"]
    assert pos["unrealized_rupees"] == pytest.approx(expected_pnl)
    assert status["today"]["open_unrealized_rupees"] == pytest.approx(expected_pnl)

    # a fetch failure degrades to "no mark", never a fake number
    monkeypatch.setattr(
        lanes, "_fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    pos2 = lanes.commodities_status()["open_positions"]["CRUDEOILM"]
    assert pos2["mark"] is None and pos2["unrealized_rupees"] is None
