import math
from datetime import datetime, timedelta

import pytest

from index_ai import market_log, strategy_lab

SESSION = "2026-09-24"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(market_log, "DB_PATH", tmp_path / "m.sqlite")
    monkeypatch.setenv("ENABLE_MARKET_LOG", "true")


def _price(spot, k, side):
    intrinsic = max(spot - k, 0) if side == "CE" else max(k - spot, 0)
    return intrinsic + 60 * math.exp(-abs(spot - k) / 200)


def _day(spots, quotes=True, start="09:30"):
    """One snapshot every 90s; OI tilts bullish (walls up + puts written)."""
    t0 = datetime.fromisoformat(f"{SESSION}T{start}:00+05:30")
    rows = []
    for i, spot in enumerate(spots):
        ts = (t0 + timedelta(seconds=90 * i)).isoformat()
        res, sup = 23700 + 50 * (i // 10), 23300 + 50 * (i // 10)
        for k in range(23000, 24001, 50):
            for side in ("CE", "PE"):
                oi = (
                    (9e6 if k == res else 1e6)
                    if side == "CE"
                    else (9e6 + 1e5 * i if k == sup else 1e6 + 2e4 * i)
                )
                p = _price(spot, k, side)
                rows.append(
                    (
                        ts,
                        SESSION,
                        "NIFTY",
                        "2026-09-29",
                        spot,
                        float(k),
                        side,
                        oi,
                        p,
                        p - 0.5 if quotes else None,
                        p + 0.5 if quotes else None,
                    )
                )
    with market_log.connect() as con:
        con.executemany(
            "INSERT INTO chain (ts, session, instrument, expiry, spot, strike, opt_type, oi,"
            " ltp, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def test_bullish_day_sells_a_bull_put_spread_and_books_real_costs(db):
    _day([23500 + 5 * i for i in range(60)])
    trades = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)
    assert trades, "a bullish read should open a spread"
    t = trades[0]
    assert t["direction"] == 1 and t["legs"][0].startswith("SELL") and t["legs"][0].endswith("PE")
    assert t["legs"][1].startswith("BUY") and t["exit_reason"] == "target"
    assert t["gross"] > 0 and t["charges"] > 40  # ≥ ₹20 x 4 executed orders, plus levies
    assert t["net"] == pytest.approx(t["gross"] - t["charges"])


def test_buy_candidate_buys_the_call_on_a_bullish_read(db):
    _day([23500 + 5 * i for i in range(60)])
    t = strategy_lab.run_session("oi_bias_buy", "NIFTY", SESSION)[0]
    assert t["legs"] == [f"BUY {t['legs'][0].split()[1]} CE"]
    assert t["gross"] > 0


def test_losing_trade_is_stopped_and_counts_against_the_strategy(db):
    # bullish OI, but the index falls hard -> the bull put spread hits its stop
    _day([23500 + 5 * i for i in range(12)] + [23555 - 25 * i for i in range(1, 40)])
    t = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)[0]
    assert t["exit_reason"] == "stop" and t["net"] < 0


def test_no_real_quotes_means_no_trade(db):
    _day([23500 + 5 * i for i in range(60)], quotes=False)
    assert strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION) == []


def test_square_off_closes_at_1510_and_mid_day_positions_are_not_counted(db):
    _day([23500 + i * 0.1 for i in range(60)], start="14:00")  # flat: no target/stop
    trades = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)
    assert [t["exit_reason"] for t in trades] == ["square-off"]
    assert trades[0]["exited"][11:16] >= "15:10"


def test_verdicts_follow_the_readiness_bar(db, monkeypatch):
    _day([23500 + 5 * i for i in range(60)])
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "COLLECTING"
    monkeypatch.setattr(strategy_lab, "MIN_TRADES", 1)
    monkeypatch.setattr(strategy_lab, "MIN_DAYS", 1)
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "PASSING"      # net ≈ +₹937 on this day
    assert rows["oi_bias_spread"]["charges"] > 80


def test_verdict_drops_a_net_negative_strategy(db, monkeypatch):
    _day([23500 + 5 * i for i in range(12)] + [23555 - 25 * i for i in range(1, 40)])
    monkeypatch.setattr(strategy_lab, "MIN_TRADES", 1)
    monkeypatch.setattr(strategy_lab, "MIN_DAYS", 1)
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "DROPPED"


def test_api_serves_the_lab(db):
    from fastapi.testclient import TestClient

    from index_ai.server import app

    _day([23500 + 5 * i for i in range(60)])
    body = TestClient(app).get("/api/strategy-lab").json()
    assert body["sessions"] == 1
    nifty = [r for r in body["rows"] if r["instrument"] == "NIFTY"]
    assert {r["strategy"] for r in nifty} == set(strategy_lab.CANDIDATES)
    assert body["recent_trades"]


def _ticks(spots, start="09:15", step_s=30):
    """Index ticks every 30s following ``spots`` with a small zig-zag."""
    t0 = datetime.fromisoformat(f"{SESSION}T{start}:00+05:30")
    rows = [((t0 + timedelta(seconds=step_s * i)).isoformat(), SESSION, "NIFTY", 13, "ticker",
             spot + (3 if i % 2 else -3)) for i, spot in enumerate(spots)]
    with market_log.connect() as con:
        con.executemany("INSERT INTO ticks (ts, session, instrument, security_id, kind, ltp)"
                        " VALUES (?,?,?,?,?,?)", rows)


def test_structure_reads_only_todays_completed_candles(db):
    _ticks([23400 + 0.5 * i for i in range(200)])       # steady climb from 09:15
    _day([23500 + 5 * i for i in range(60)], start="09:15")
    snaps = strategy_lab.oi_signals.load_session("NIFTY", SESSION)
    sigs = strategy_lab.signals("NIFTY", SESSION, snaps)
    by_time = {snaps[i][0][11:16]: sigs[i]["structure"] for i in range(1, len(snaps))}
    assert by_time["09:30"] == "RANGE"                   # 3 candles: not enough of today yet
    assert by_time["10:30"] == "UP"


def test_price_action_candidates_trade_with_and_against_the_structure(db):
    _ticks([23400 + 0.5 * i for i in range(400)])
    _day([23500 + 5 * i for i in range(60)], start="09:15")
    with_ = strategy_lab.run_session("pa_structure_spread", "NIFTY", SESSION)
    fade = strategy_lab.run_session("pa_fade_spread", "NIFTY", SESSION)
    assert with_ and with_[0]["legs"][0].endswith("PE") and with_[0]["entered"][11:16] >= "09:50"
    assert fade and fade[0]["legs"][0].endswith("CE")


def test_no_recorded_ticks_means_no_price_action_trade(db):
    _day([23500 + 5 * i for i in range(60)])
    assert strategy_lab.run_session("pa_structure_spread", "NIFTY", SESSION) == []
