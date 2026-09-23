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
