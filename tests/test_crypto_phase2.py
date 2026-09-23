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
    # default (2026-09-23): session end no longer closes it -- stop/trail/max-hold do
    kept, ev = nb.step(
        "BTCUSD", df5, df15, state=dict(state), cfg=nb.NBreakConfig(),
        in_session=False, session_date="2026-09-07",
    )
    assert ev["event"] != "exit" and kept["position"]
    # with signal exits switched back on, session end forces the exit
    state, ev = nb.step(
        "BTCUSD",
        df5,
        df15,
        state=state,
        cfg=nb.NBreakConfig(signal_exits=True),
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
    # lane tests close positions via NY session end, a signal exit that is
    # off by default since 2026-09-23 -- switch it back on for them
    import dataclasses

    _orig_nb = lanes._nb_cfg
    monkeypatch.setattr(lanes, "_nb_cfg", lambda st: dataclasses.replace(_orig_nb(st), signal_exits=True))
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


def test_rejected_entry_does_not_burn_the_session_trade_budget(paper_env, monkeypatch):
    """A signal that fires "enter" but gets rejected downstream (sizing, in
    this test, via a $1 deploy cap) must not consume ny_n_break's per-session
    trade count or the swing level it detected — otherwise a strategy that
    keeps getting capacity-rejected silently uses up its whole session on
    trades that never actually happened."""
    monkeypatch.setenv("CRYPTO_DEPLOY_USD", "1")
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
    ev = next(e for e in events if e.get("asset") == "BTCUSD" and e.get("strategy") == "ny_n_break")
    assert ev["event"] == "wait" and "sizing" in ev.get("reason", "")

    # rolled all the way back to "as if this tick never ran" — on the very
    # first tick for a fresh symbol that means no strategy state was saved at
    # all yet, which is fine: the next scan re-derives long_lvl/short_lvl from
    # the candles fresh, so nothing about the setup is actually lost.
    strat_state = journal.load_state().get("ny_n_break:BTCUSD", {}).get("strategy")
    assert strat_state is None or strat_state.get("trades_today", 0) == 0


def test_lane_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_AK_ROXX_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_CPR_TREND_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_RSI_ADX_TREND_ENABLED", "false")
    assert lanes.scan_crypto_paper() == []
    assert lanes.enabled() is False


def test_rsi_adx_trend_restricted_to_its_proven_symbols(monkeypatch):
    # net-positive on BTC/ETH/SOL, net-negative on PAXG/XRP/BNB (RESULTS.md) —
    # the lane must not silently expand it to the full configured symbol list.
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSD,ETHUSD,SOLUSD,PAXGUSD,XRPUSD,BNBUSD")
    from crypto.config import crypto_settings

    s = crypto_settings()
    assert lanes._symbols_for("rsi_adx_trend", s) == ("BTCUSD", "ETHUSD", "SOLUSD")
    # an unrestricted strategy still gets the full configured list
    assert set(lanes._symbols_for("ichimoku", s)) == set(s.symbols)


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


def test_close_position_manual_closes_at_the_current_mark(paper_env, monkeypatch):
    """The dashboard's manual "Close" button — must journal the same shape of
    row the automatic exits do, with the right P&L off the live mark."""
    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 63000.0})
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"position": "whatever the strategy's own view was"},
                "position": {
                    "strategy": "ny_n_break",
                    "asset": "BTCUSD",
                    "side": "long",
                    "day": "2026-09-08",
                    "entry_price": 60000.0,
                    "entry_time": "2026-09-08T10:00:00+00:00",
                    "opened_at": "2026-09-08T10:00:00+00:00",
                    "size": 30,
                    "contract_value": 0.001,
                    "leverage": 20,
                    "margin_total_usd": 90.0,
                    "notional_usd": 1800.0,
                    "mode": "paper",
                },
            }
        }
    )
    result = lanes.close_position_manual("ny_n_break:BTCUSD")
    assert result["ok"] is True
    row = result["trade"]
    assert row["exit_reason"] == "manual close" and row["exit_price"] == 63000.0
    gross = (63000.0 - 60000.0) * (30 * 0.001)  # long, coins = size * contract_value
    assert row["gross_usd"] == pytest.approx(gross, abs=0.01)
    assert row["pnl_usd"] < row["gross_usd"]  # fees came off

    st = journal.load_state()
    assert st["ny_n_break:BTCUSD"]["position"] is None
    assert (
        st["ny_n_break:BTCUSD"]["strategy"]["position"] is None
    )  # strategy's own view cleared too
    assert (
        journal.recent()[-1]["exit_id"] == row["exit_id"]
    )  # actually journalled, not just returned

    # closing again (or a key with nothing open) fails cleanly, no crash
    again = lanes.close_position_manual("ny_n_break:BTCUSD")
    assert again["ok"] is False and "no open position" in again["error"]


def _open_pos(strategy, asset, entry, day="2026-09-08"):
    return {
        "strategy": {"position": "whatever the strategy's own view was"},
        "position": {
            "strategy": strategy,
            "asset": asset,
            "side": "long",
            "day": day,
            "entry_price": entry,
            "entry_time": "2026-09-08T10:00:00+00:00",
            "opened_at": "2026-09-08T10:00:00+00:00",
            "size": 10,
            "contract_value": 0.001,
            "leverage": 20,
            "margin_total_usd": 30.0,
            "notional_usd": 600.0,
            "mode": "paper",
        },
    }


def test_close_all_closes_every_open_position(paper_env, monkeypatch):
    """The "Close all" button — closes every open key, one call to
    close_position_manual per key, and reports which succeeded/failed."""
    monkeypatch.setattr(lanes.market_data, "ticker", lambda sym, **k: {"mark_price": 63000.0})
    journal.save_state(
        {
            "ny_n_break:BTCUSD": _open_pos("ny_n_break", "BTCUSD", 60000.0),
            "ichimoku:BTCUSD": _open_pos("ichimoku", "BTCUSD", 61000.0),
            "ak_roxx_pro:ETHUSD": {
                "strategy": {"position": None},
                "position": None,
            },  # nothing open
        }
    )
    result = lanes.close_all_positions_manual()
    assert result["ok"] is True
    assert result["attempted"] == 2  # only the two with an actual open position
    assert set(result["closed"]) == {"ny_n_break:BTCUSD", "ichimoku:BTCUSD"}
    assert result["failed"] == {}

    st = journal.load_state()
    assert st["ny_n_break:BTCUSD"]["position"] is None
    assert st["ichimoku:BTCUSD"]["position"] is None
    assert len(journal.recent()) == 2

    # nothing open → a clean no-op, not an error
    empty = lanes.close_all_positions_manual()
    assert empty == {"ok": True, "attempted": 0, "closed": [], "failed": {}}


def test_close_all_reports_a_failure_without_stopping_the_rest(paper_env, monkeypatch):
    """One key with no live price must fail on its own without blocking the
    other keys from closing."""

    def flaky_ticker(sym, **k):
        if sym == "ETHUSD":
            raise RuntimeError("no feed")
        return {"mark_price": 63000.0}

    monkeypatch.setattr(lanes.market_data, "ticker", flaky_ticker)
    journal.save_state(
        {
            "ny_n_break:BTCUSD": _open_pos("ny_n_break", "BTCUSD", 60000.0),
            "ak_roxx_pro:ETHUSD": _open_pos("ak_roxx_pro", "ETHUSD", 2500.0),
        }
    )
    result = lanes.close_all_positions_manual()
    assert result["ok"] is False
    assert result["closed"] == ["ny_n_break:BTCUSD"]
    assert "ak_roxx_pro:ETHUSD" in result["failed"]
    assert journal.load_state()["ak_roxx_pro:ETHUSD"]["position"] is not None  # untouched


def test_close_position_manual_waits_for_a_scan_holding_the_lock(paper_env, monkeypatch):
    """A manual close and the ~60s scan loop both mutate crypto_state.json /
    the journal -- they must never interleave. Prove _STATE_LOCK actually
    blocks a manual close until whoever holds it (here, a stand-in for a
    scan in progress) releases it."""
    import threading
    import time

    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 100.0})
    journal.save_state(
        {
            "ny_n_break:BTCUSD": {
                "strategy": {"position": "x"},
                "position": {
                    "strategy": "ny_n_break",
                    "asset": "BTCUSD",
                    "side": "long",
                    "day": "2026-09-08",
                    "entry_price": 100.0,
                    "entry_time": "2026-09-08T10:00:00+00:00",
                    "opened_at": "2026-09-08T10:00:00+00:00",
                    "size": 30,
                    "contract_value": 0.001,
                    "leverage": 20,
                    "margin_total_usd": 15.0,
                    "notional_usd": 300.0,
                    "mode": "paper",
                },
            }
        }
    )

    order: list[str] = []

    def hold_lock_briefly():
        with lanes._STATE_LOCK:  # stand-in for a scan cycle in progress
            order.append("scan-start")
            time.sleep(0.1)
            order.append("scan-end")

    # record from *inside* the locked section the close actually runs, not just
    # from the outer call -- otherwise this proves nothing about the lock (a
    # close with the lock ripped out would log the same two outer timestamps)
    real_locked = lanes._close_position_manual_locked

    def recording_locked(key, client):
        order.append("close-start")
        result = real_locked(key, client)
        order.append("close-end")
        return result

    monkeypatch.setattr(lanes, "_close_position_manual_locked", recording_locked)

    t = threading.Thread(target=hold_lock_briefly)
    t.start()
    time.sleep(0.02)  # let the thread grab the lock first
    result = lanes.close_position_manual("ny_n_break:BTCUSD")  # must block until released
    t.join()

    # "close-start" can only appear once _STATE_LOCK is acquired, which can only
    # happen after hold_lock_briefly's "scan-end" releases it -- without the
    # lock, close (started ~0.02s in) would finish well before scan releases
    # it at ~0.1s, giving scan-start/close-start/close-end/scan-end instead.
    assert order == ["scan-start", "scan-end", "close-start", "close-end"]
    assert result["ok"] is True


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
