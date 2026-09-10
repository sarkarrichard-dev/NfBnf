"""Each crypto strategy trades its own independent book; nothing is held past
the max-hold cap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from crypto import journal, lanes
from crypto.config import crypto_settings
from crypto.delta.products import Contract

_UTC = timezone.utc


def test_open_counts_are_per_strategy():
    st = {
        "ny_n_break:BTCUSD": {"position": {"side": "long"}},
        "ny_n_break:ETHUSD": {"position": {"side": "long"}},
        "ichimoku:BTCUSD": {"position": {"side": "short"}},
        "ak_roxx_pro:BTCUSD": {"position": None},
        "_meta": {"whatever": 1},
    }
    counts = lanes._open_counts(st)
    assert counts["ny_n_break"] == 2
    assert counts["ichimoku"] == 1
    assert counts["ak_roxx_pro"] == 0


@pytest.mark.parametrize(
    "opened, now, max_days, expected",
    [
        (
            "2026-09-10T23:00:00+00:00",
            datetime(2026, 9, 11, 4, 0, tzinfo=_UTC),
            1,
            False,
        ),  # next day, ok
        (
            "2026-09-10T01:00:00+00:00",
            datetime(2026, 9, 11, 23, 0, tzinfo=_UTC),
            1,
            False,
        ),  # still 1 day
        (
            "2026-09-10T23:00:00+00:00",
            datetime(2026, 9, 12, 0, 5, tzinfo=_UTC),
            1,
            True,
        ),  # 2 days → close
        ("2026-09-10T00:00:00+00:00", datetime(2026, 9, 13, 0, 0, tzinfo=_UTC), 1, True),
        (
            "2026-09-10T00:00:00+00:00",
            datetime(2026, 9, 13, 0, 0, tzinfo=_UTC),
            3,
            False,
        ),  # bigger cap
        (None, datetime(2026, 9, 20, tzinfo=_UTC), 1, False),
    ],
)
def test_hold_exceeded(opened, now, max_days, expected):
    assert lanes._hold_exceeded({"opened_at": opened}, now, max_days) is expected


def _blank_ev():
    return {
        "strategy": "ny_n_break",
        "asset": "BTCUSD",
        "event": "enter",
        "side": "long",
        "price": 60000.0,
    }


def _frame():
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-10", periods=3, freq="5min", tz="UTC"),
            "open": [1, 1, 1],
            "high": [1, 1, 1],
            "low": [1, 1, 1],
            "close": [60000.0, 60000.0, 60000.0],
            "volume": [1, 1, 1],
        }
    )


def test_apply_entry_blocks_at_per_strategy_cap(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_CONCURRENT", "2")
    monkeypatch.setenv("CRYPTO_MAX_OPEN_TOTAL", "0")
    s = crypto_settings()
    contract = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    ns, slot = {}, {}
    ev = _blank_ev()
    # this strategy already holds 2 → blocked, even though the portfolio is otherwise empty
    lanes._apply_entry(
        ev,
        ns,
        slot,
        s,
        contract,
        "ny_n_break",
        "BTCUSD",
        "d",
        datetime.now(_UTC),
        strat_open=2,
        total_open=2,
        frame=_frame(),
    )
    assert ev["event"] == "wait" and "per strategy" in ev["reason"]
    assert not slot.get("position")

    # a different strategy with 0 open is NOT blocked by ny_n_break's 2
    ev2 = {**_blank_ev(), "strategy": "ichimoku"}
    monkeypatch.setattr(
        lanes,
        "size_position",
        lambda *a, **k: type(
            "R",
            (),
            {"ok": True, "size": 1, "leverage": 100.0, "margin_total_usd": 5.0, "reason": ""},
        )(),
    )
    monkeypatch.setattr(lanes, "_entry_features", lambda *a, **k: {})
    monkeypatch.setattr(lanes.ml_gate, "check", lambda t: {"allowed": True, "reason": ""})
    lanes._apply_entry(
        ev2,
        {},
        {},
        s,
        contract,
        "ichimoku",
        "BTCUSD",
        "d",
        datetime.now(_UTC),
        strat_open=0,
        total_open=2,
        frame=_frame(),
    )
    assert ev2["event"] != "wait" or "per strategy" not in ev2.get("reason", "")


def test_apply_entry_blocks_at_portfolio_cap(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_CONCURRENT", "5")
    monkeypatch.setenv("CRYPTO_MAX_OPEN_TOTAL", "4")
    s = crypto_settings()
    contract = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    ev = _blank_ev()
    lanes._apply_entry(
        ev,
        {},
        {},
        s,
        contract,
        "ny_n_break",
        "BTCUSD",
        "d",
        datetime.now(_UTC),
        strat_open=1,
        total_open=4,
        frame=_frame(),
    )
    assert ev["event"] == "wait" and "portfolio cap" in ev["reason"]


def test_scan_force_closes_a_stale_position_the_strategy_wont_exit(tmp_path, monkeypatch):
    """End-to-end: a lane position opened 3 days ago, strategy says 'hold' →
    the lane force-closes it and journals a 'max hold' row."""
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSD")
    monkeypatch.setenv("CRYPTO_MAX_HOLD_DAYS", "1")
    monkeypatch.setenv("CRYPTO_USDINR", "88")
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_API_SECRET", raising=False)
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")

    contract = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    monkeypatch.setattr(lanes.products, "all_contracts", lambda client=None: {"BTCUSD": contract})
    monkeypatch.setattr(lanes.charges, "sample_spread", lambda *a, **k: None)
    monkeypatch.setattr(lanes.market_data, "depth", lambda *a, **k: {})
    fr = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-13", periods=30, freq="5min", tz="UTC"),
            "open": [60000.0] * 30,
            "high": [60100.0] * 30,
            "low": [59900.0] * 30,
            "close": [60500.0] * 30,
            "volume": [10.0] * 30,
        }
    )
    monkeypatch.setattr(lanes.market_data, "candles", lambda *a, **k: fr)
    # strategy is happy to keep holding
    monkeypatch.setattr(
        lanes.nb,
        "step",
        lambda *a, **k: ({"position": {"side": "long"}}, {"event": "hold", "side": "long"}),
    )

    opened = (datetime.now(_UTC) - timedelta(days=4)).replace(microsecond=0).isoformat()
    stale = {
        "strategy": "ny_n_break",
        "asset": "BTCUSD",
        "side": "long",
        "day": opened[:10],
        "mode": "paper",
        "entry_price": 60000.0,
        "entry_time": opened,
        "size": 1,
        "contract_value": 0.001,
        "leverage": 100.0,
        "margin_total_usd": 5.0,
        "notional_usd": 60.0,
        "opened_at": opened,
    }
    journal.save_state(
        {"ny_n_break:BTCUSD": {"strategy": {"position": {"side": "long"}}, "position": stale}}
    )

    events = lanes.scan_crypto_paper()
    assert any("max hold" in str(e.get("reason", "")) for e in events), events
    rows = journal.recent()
    assert len(rows) == 1 and "max hold" in rows[0]["exit_reason"]
    assert journal.load_state()["ny_n_break:BTCUSD"].get("position") is None
