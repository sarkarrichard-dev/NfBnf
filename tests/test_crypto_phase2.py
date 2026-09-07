"""Phase 2 crypto — session, sizing, strategy engines, and the paper lane end to end.

No network: Delta market data and the contract master are monkeypatched.
"""

from __future__ import annotations

import pandas as pd
import pytest

from crypto import journal, lanes, notify, session
from crypto.delta.products import Contract
from crypto.sizing import size_position
from crypto.strategies import ichimoku as ichi
from crypto.strategies import ny_n_break as nb
from index_ai.market_clock import IST


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def test_ny_window_and_day():
    from datetime import datetime

    inside = datetime(2026, 9, 7, 19, 30, tzinfo=IST)
    outside = datetime(2026, 9, 7, 9, 0, tzinfo=IST)
    assert session.in_ny_window("18:00", "23:00", inside)
    assert not session.in_ny_window("18:00", "23:00", outside)
    assert session.ny_session_date("18:00", "23:00", inside) == "2026-09-07"


# ---------------------------------------------------------------------------
# sizing
# ---------------------------------------------------------------------------
def test_sizing_hundred_dollars_three_x():
    btc = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    r = size_position(btc, 60_000, deploy_usd=100, leverage=3, wallet_usd=2000)
    assert r.ok and r.size == 4
    assert r.leverage == 3.0
    # a small bankroll blocks the trade rather than over-sizing it
    r2 = size_position(btc, 60_000, deploy_usd=100, leverage=3, wallet_usd=15)
    assert not r2.ok


# ---------------------------------------------------------------------------
# strategy engines
# ---------------------------------------------------------------------------
def _nbreak_5m(n: int = 60) -> pd.DataFrame:
    base = (
        list(range(100, 140))
        + [138, 136, 135, 137, 139, 141, 143, 145, 147, 149]
        + list(range(150, 160))
    )
    while len(base) < n:
        base.append(base[-1] + 1)
    closes = [float(x) for x in base[:n]]
    return pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-09-07 18:00", periods=n, freq="5min", tz="Asia/Kolkata"
            ),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def test_ny_n_break_enters_on_the_rebreak():
    df5 = _nbreak_5m()
    df15 = df5.iloc[::3].reset_index(drop=True)
    state, fired = None, None
    for i in range(30, len(df5)):
        state, ev = nb.step(
            "BTCUSD",
            df5.iloc[: i + 1],
            df15,
            state=state,
            cfg=nb.NBreakConfig(),
            in_session=True,
            session_date="2026-09-07",
        )
        if ev["event"] == "enter":
            fired = ev
            break
    assert fired and fired["side"] == "long"
    # session end forces the exit
    state, ev = nb.step(
        "BTCUSD",
        df5,
        df15,
        state=state,
        cfg=nb.NBreakConfig(),
        in_session=False,
        session_date="2026-09-07",
    )
    assert ev["event"] == "exit" and ev["reason"] == "session end"


def test_ichimoku_enters_long_on_trend_turn():
    n = 160
    closes = [120 - 0.15 * i for i in range(90)] + [106.5 + 1.2 * i for i in range(70)]
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-08-01", periods=n, freq="1h", tz="UTC"),
            "open": closes[:n],
            "high": [c + 1 for c in closes[:n]],
            "low": [c - 1 for c in closes[:n]],
            "close": closes[:n],
            "volume": [5.0] * n,
        }
    )
    state, saw = None, False
    for i in range(83, n):
        state, ev = ichi.step("BTCUSD", df.iloc[: i + 1], state=state, cfg=ichi.IchimokuConfig())
        if ev["event"] == "enter":
            saw = ev["side"] == "long"
            break
    assert saw


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------
def test_journal_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "j.jsonl")
    journal.save_state({"k": {"position": None}})
    assert journal.load_state()["k"]["position"] is None
    journal.journal({"day": "2026-09-07", "strategy": "ichimoku", "pnl_usd": 4.0})
    assert journal.recent()[-1]["pnl_usd"] == 4.0


# ---------------------------------------------------------------------------
# lane — end to end, paper, no network
# ---------------------------------------------------------------------------
@pytest.fixture
def paper_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_CRYPTO_PAPER", "true")
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_USDINR", "88")
    monkeypatch.setenv("CRYPTO_PAPER_BANKROLL", "5000")
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")

    contracts = {
        "BTCUSD": Contract("BTCUSD", 27, 0.001, 0.5, 1, 100),
        "ETHUSD": Contract("ETHUSD", 3136, 0.01, 0.05, 1, 100),
    }
    monkeypatch.setattr(lanes.products, "all_contracts", lambda client=None: contracts)

    opened: list = []
    closed: list = []
    monkeypatch.setattr(notify, "opened", lambda p: opened.append(p))
    monkeypatch.setattr(notify, "closed", lambda r: closed.append(r))
    monkeypatch.setattr(notify, "day_summary", lambda *a, **k: None)
    # a 5m frame whose last *closed* bar (after the forming bar is dropped) is a
    # fresh re-break above the swing high at 135
    closes = (
        list(range(100, 131))
        + [131, 132, 133, 134, 135, 134, 133, 132]
        + [133, 131, 129, 128, 128, 129, 130, 132, 133, 134, 134, 133, 134, 136, 130]
    )
    df5 = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-07 18:00", periods=len(closes), freq="5min", tz=IST),
            "open": [float(c) for c in closes],
            "high": [float(c) + 1 for c in closes],
            "low": [float(c) - 1 for c in closes],
            "close": [float(c) for c in closes],
            "volume": [10.0] * len(closes),
        }
    )
    # DeltaClient with no creds → _fx_rate falls back to CRYPTO_USDINR
    return {"opened": opened, "closed": closed, "df5": df5}


def test_lane_opens_and_journals_a_paper_trade(paper_env, monkeypatch):
    df5 = paper_env["df5"]
    df15 = df5.iloc[::3].reset_index(drop=True)
    flat = df5.assign(close=130.0, open=130.0, high=131.0, low=129.0)

    def fake_candles(symbol, resolution, *, days=3.0, client=None):
        if symbol == "BTCUSD" and resolution == "5m":
            return df5
        if symbol == "BTCUSD" and resolution == "15m":
            return df15
        return flat

    monkeypatch.setattr(lanes.market_data, "candles", fake_candles)
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "ny_session_date", lambda *a, **k: "2026-09-07")

    events = lanes.scan_crypto_paper()
    assert any(e.get("event") == "enter" and e.get("asset") == "BTCUSD" for e in events)
    assert len(paper_env["opened"]) == 1
    st = journal.load_state()
    assert st["ny_n_break:BTCUSD"]["position"]["side"] == "long"
    assert st["ny_n_break:BTCUSD"]["position"]["size"] >= 1

    # next scan: session over → exit → one journal row with USD and INR pnl
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: False)
    lanes.scan_crypto_paper()
    rows = journal.recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] == "ny_n_break" and row["asset"] == "BTCUSD"
    assert "pnl_usd" in row and "pnl_inr" in row and row["fx_usdinr"] == 88.0
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None
    assert len(paper_env["closed"]) == 1


def test_lane_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CRYPTO_PAPER", "false")
    assert lanes.scan_crypto_paper() == []
