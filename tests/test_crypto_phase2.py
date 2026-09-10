"""Phase 2 crypto — session, sizing, strategy engines, and the paper lane end to end.

No network: Delta market data and the contract master are monkeypatched.
"""

from __future__ import annotations

import pandas as pd
import pytest

from crypto import journal, lanes, session
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
def test_sizing_lot_based():
    btc = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    r = size_position(btc, 60_000, lots=2, leverage=3, wallet_usd=2000)
    assert r.ok and r.size == 2
    assert r.leverage == 3.0
    # a small bankroll blocks the trade rather than over-sizing it
    assert not size_position(btc, 60_000, lots=2, leverage=3, wallet_usd=15).ok
    # deploy_usd is an optional cap
    assert not size_position(btc, 60_000, lots=3, leverage=3, wallet_usd=5000, deploy_usd=40).ok


def test_trailing_stop_exits_a_position():
    from crypto.strategies.trailing import TrailConfig, update_and_check

    # pin the classic step values — this tests the ratchet, not the module default
    cfg = TrailConfig(
        leverage=100.0,
        stop_pnl_pct=10.0,
        ratchet_step_pnl_pct=5.0,
        tp_trigger_pnl_pct=25.0,
        peak_trail_pnl_pct=2.0,
    )  # 1% price move = 100% P&L
    pos = {"entry_price": 100.0, "side": "long"}
    # run to +15% P&L (price +0.15%) — stop ratchets to +5%
    assert update_and_check(pos, 100.15, cfg) is None
    assert pos["trail_stop_pnl_pct"] == 5.0
    # give back to +4% P&L — below the +5% stop → exit
    reason = update_and_check(pos, 100.04, cfg)
    assert reason and "trailing" in reason


def test_sizing_rejects_an_insane_mark():
    btc = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    # a 10x-off / corrupted feed price is refused, not silently sized off
    assert not size_position(btc, 6.0, lots=1, leverage=3, wallet_usd=2000).ok
    assert not size_position(btc, 60_000_000, lots=1, leverage=3, wallet_usd=1e9).ok


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
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_NBREAK_ALLROUND", "false")  # these tests exercise the NY-window gate
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_AK_ROXX_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSD,ETHUSD")
    monkeypatch.setenv("CRYPTO_USDINR", "88")
    monkeypatch.setenv("CRYPTO_PAPER_BANKROLL", "5000")
    # paper lane: no Delta creds → _fx_rate uses the CRYPTO_USDINR fallback,
    # never a real wallet call (the repo .env may carry real keys)
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_API_SECRET", raising=False)
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")

    contracts = {
        "BTCUSD": Contract("BTCUSD", 27, 0.001, 0.5, 1, 100),
        "ETHUSD": Contract("ETHUSD", 3136, 0.01, 0.05, 1, 100),
    }
    monkeypatch.setattr(lanes.products, "all_contracts", lambda client=None: contracts)

    monkeypatch.setattr(lanes.charges, "sample_spread", lambda *a, **k: None)
    monkeypatch.setattr(lanes.market_data, "depth", lambda *a, **k: {})
    # the lane-level entry window is always open in these tests — they exercise
    # the per-strategy NY-window gate via lanes.in_ny_window, not this one.
    monkeypatch.setattr(lanes, "in_crypto_session", lambda *a, **k: True)
    # a 5m frame whose last *closed* bar (after the forming bar is dropped) is a
    # fresh re-break above the swing high — scaled to a realistic BTC price so the
    # sizer's sanity band accepts the mark
    base = (
        list(range(100, 131))
        + [131, 132, 133, 134, 135, 134, 133, 132]
        + [133, 131, 129, 128, 128, 129, 130, 132, 133, 134, 134, 133, 134, 136, 130]
    )
    closes = [c * 450.0 for c in base]
    df5 = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-07 18:00", periods=len(closes), freq="5min", tz=IST),
            "open": closes,
            "high": [c + 450 for c in closes],
            "low": [c - 450 for c in closes],
            "close": closes,
            "volume": [10.0] * len(closes),
        }
    )
    # DeltaClient with no creds → _fx_rate falls back to CRYPTO_USDINR
    return {"df5": df5}


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

    # simulate a crash between the journal append and the state save: both the
    # lane position and the strategy position come back. Next scan must NOT
    # double-count — the exit_id already in the journal is honoured.
    st = journal.load_state()
    pos = {
        "strategy": "ny_n_break",
        "asset": "BTCUSD",
        "side": "long",
        "day": "2026-09-07",
        "entry_price": row["entry_price"],
        "entry_time": row["entry_time"],
        "opened_at": row.get("opened_at"),
        "size": row["size"],
        "contract_value": 0.001,
        "leverage": row["leverage"],
        "margin_total_usd": row["margin_usd"],
        "notional_usd": row["notional_usd"],
        "stop_price": None,
    }
    st["ny_n_break:BTCUSD"]["position"] = dict(pos)
    st["ny_n_break:BTCUSD"]["strategy"]["position"] = {
        "side": "long",
        "entry_price": row["entry_price"],
        "entry_time": row["entry_time"],
    }
    journal.save_state(st)
    lanes.scan_crypto_paper()  # session still over → strategy re-emits "exit"
    assert len(journal.recent()) == 1  # still one row, not two
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"] is None


def test_lane_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_AK_ROXX_ENABLED", "false")
    assert lanes.scan_crypto_paper() == []
    assert lanes.enabled() is False


def test_nbreak_allround_takes_the_setup_outside_the_ny_window(paper_env, monkeypatch):
    monkeypatch.setenv("CRYPTO_NBREAK_ALLROUND", "true")
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
    # well outside 18:00–23:00 IST — the window gate would block this
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: False)

    events = lanes.scan_crypto_paper()
    assert any(e.get("event") == "enter" and e.get("asset") == "BTCUSD" for e in events)
    assert journal.load_state()["ny_n_break:BTCUSD"]["position"]["side"] == "long"


def test_lane_session_window_gates_entries_not_the_scan(paper_env, monkeypatch):
    """New entries fire only inside the lane window (default 16:00-06:00 IST);
    outside it the scan still runs — it just doesn't open anything."""
    monkeypatch.setenv("CRYPTO_NBREAK_ALLROUND", "true")  # strategy itself isn't the gate
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

    # outside the lane window → no position, a "wait" event names the reason.
    # (The same setup entering *inside* the window is covered by every other
    # entry test — the paper_env fixture holds in_crypto_session open.)
    monkeypatch.setattr(lanes, "in_crypto_session", lambda *a, **k: False)
    events = lanes.scan_crypto_paper()
    assert not any(e.get("event") == "enter" for e in events)
    assert any("outside crypto session" in str(e.get("reason", "")) for e in events)
    assert journal.load_state().get("ny_n_break:BTCUSD", {}).get("position") is None


def test_prune_removed_strategy_closes_and_drops_the_orphan_slot(paper_env, monkeypatch):
    df5 = paper_env["df5"]
    flat = df5.assign(close=130.0, open=130.0, high=131.0, low=129.0)
    monkeypatch.setattr(lanes.market_data, "candles", lambda *a, **k: flat)
    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 131.0})
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: False)

    # a leftover position for a strategy the code no longer has
    journal.save_state(
        {
            "candle_renko:BTCUSD": {
                "position": {
                    "strategy": "candle_renko",
                    "asset": "BTCUSD",
                    "side": "short",
                    "day": "2026-09-08",
                    "entry_price": 130.0,
                    "entry_time": "2026-09-08T10:00:00+00:00",
                    "opened_at": "2026-09-08T10:00:00+00:00",
                    "size": 30,
                    "contract_value": 0.001,
                    "leverage": 100,
                    "margin_total_usd": 1.35,
                    "notional_usd": 3.9,
                    "mode": "paper",
                }
            }
        }
    )
    lanes.scan_crypto_paper()

    st = journal.load_state()
    assert "candle_renko:BTCUSD" not in st  # slot dropped
    rows = [r for r in journal.recent() if r["strategy"] == "candle_renko"]
    assert len(rows) == 1 and rows[0]["exit_reason"] == "strategy removed"
