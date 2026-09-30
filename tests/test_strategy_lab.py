import calendar
import math
from datetime import datetime, timedelta

import pandas as pd
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


def _day(spots, quotes=True, start="09:30", expiry="2026-09-29"):
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
                        expiry,
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


# climbs 195 pts, then turns -- the 1:1 index trail (NIFTY sells 40) closes it
RISE_THEN_TURN = [23500 + 5 * i for i in range(40)] + [23695 - 10 * i for i in range(1, 20)]


def test_bullish_day_sells_a_bull_put_spread_and_books_real_costs(db):
    _day(RISE_THEN_TURN)
    trades = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)
    assert trades, "a bullish read should open a spread"
    t = trades[0]
    assert t["direction"] == 1 and t["legs"][0].startswith("SELL") and t["legs"][0].endswith("PE")
    assert t["legs"][1].startswith("BUY") and t["exit_reason"] == "trail stop"
    assert t["gross"] > 0 and t["charges"] > 40  # ≥ ₹20 x 4 executed orders, plus levies
    assert t["net"] == pytest.approx(t["gross"] - t["charges"])


def test_buy_candidate_buys_the_call_on_a_bullish_read(db):
    # climbs 200 pts, then turns: the 25-pt 1:1 trail locks most of the move
    _day([23500 + 5 * i for i in range(40)] + [23695 - 10 * i for i in range(1, 20)])
    t = strategy_lab.run_session("oi_bias_buy", "NIFTY", SESSION)[0]
    assert t["legs"] == [f"BUY {t['legs'][0].split()[1]} CE"]
    assert t["exit_reason"] == "trail stop" and t["gross"] > 0


def test_losing_trade_is_stopped_and_counts_against_the_strategy(db):
    # bullish OI, but the index falls hard -> the bull put spread hits its stop
    _day([23500 + 5 * i for i in range(12)] + [23555 - 25 * i for i in range(1, 40)])
    t = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)[0]
    assert t["exit_reason"] == "trail stop" and t["net"] < 0


def test_no_real_quotes_means_no_trade(db):
    _day([23500 + 5 * i for i in range(60)], quotes=False)
    assert strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION) == []


def test_square_off_closes_at_1510_and_mid_day_positions_are_not_counted(db):
    _day([23500 + i * 0.1 for i in range(60)], start="14:00")  # flat: no target/stop
    trades = strategy_lab.run_session("oi_bias_spread", "NIFTY", SESSION)
    assert [t["exit_reason"] for t in trades] == ["square-off"]
    assert trades[0]["exited"][11:16] >= "15:10"


def test_verdicts_follow_the_readiness_bar(db, monkeypatch):
    _day(RISE_THEN_TURN)
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "COLLECTING"
    monkeypatch.setattr(strategy_lab, "MIN_TRADES", 1)
    monkeypatch.setattr(strategy_lab, "MIN_DAYS", 1)
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "PASSING"
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

    _day(RISE_THEN_TURN)
    body = TestClient(app).get("/api/strategy-lab").json()
    assert body["sessions"] == 1
    nifty = [r for r in body["rows"] if r["instrument"] == "NIFTY"]
    assert {r["strategy"] for r in nifty} == set(strategy_lab.CANDIDATES) - strategy_lab.ON_DEMAND
    assert body["recent_trades"]


def _ticks(spots, start="09:15", step_s=30):
    """Index ticks every 30s following ``spots`` with a small zig-zag."""
    t0 = datetime.fromisoformat(f"{SESSION}T{start}:00+05:30")
    # Dhan's ltt: IST wall-clock seconds stored as an epoch
    ist_epoch = lambda t: calendar.timegm(t.timetuple())  # wall clock read as UTC  # noqa: E731
    rows = []
    for i, spot in enumerate(spots):
        t = t0 + timedelta(seconds=step_s * i)
        rows.append(
            (
                t.isoformat(),
                SESSION,
                "NIFTY",
                13,
                "ticker",
                spot + (3 if i % 2 else -3),
                ist_epoch(t),
            )
        )
    with market_log.connect() as con:
        con.executemany(
            "INSERT INTO ticks (ts, session, instrument, security_id, kind, ltp, ltt)"
            " VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def test_structure_reads_only_todays_completed_candles(db):
    _ticks([23400 + 0.5 * i for i in range(200)])  # steady climb from 09:15
    _day([23500 + 5 * i for i in range(60)], start="09:15")
    snaps = strategy_lab.oi_signals.load_session("NIFTY", SESSION)
    sigs = strategy_lab.signals("NIFTY", SESSION, snaps)
    by_time = {snaps[i][0][11:16]: sigs[i]["structure"] for i in range(1, len(snaps))}
    assert by_time["09:30"] == "RANGE"  # 3 candles: not enough of today yet
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


def _set_iv(iv):
    with market_log.connect() as con:
        con.execute("UPDATE chain SET iv=?", (iv,))


def test_expensive_options_gate(db):
    _ticks([23400 + 0.5 * i for i in range(600)])  # calm climb: small actual move
    _day([23500 + 5 * i for i in range(120)], start="09:15")
    assert strategy_lab.run_session("pa_structure_spread", "NIFTY", SESSION)
    _set_iv(20.0)  # options price in far more than that
    rich = strategy_lab.run_session("vrp_structure_spread", "NIFTY", SESSION)
    assert rich and rich[0]["legs"][0].endswith("PE")  # same direction rule: bull put
    assert rich[0]["entered"][11:16] >= "10:20"  # needs an hour of today's candles
    _set_iv(0.00001)  # options cheap vs the actual move
    assert strategy_lab.run_session("vrp_structure_spread", "NIFTY", SESSION) == []


def test_no_recorded_iv_means_no_gated_trade(db):
    _ticks([23400 + 0.5 * i for i in range(600)])
    _day([23500 + 5 * i for i in range(120)], start="09:15")
    assert strategy_lab.run_session("vrp_bias_spread", "NIFTY", SESSION) == []


def _scale_near(f, expiry="2026-09-24"):
    with market_log.connect() as con:
        con.execute(
            "UPDATE chain SET ltp=ltp*?, bid=bid*?, ask=ask*? WHERE expiry=?", (f, f, f, expiry)
        )


def test_thin_near_premium_switches_to_next_expiry(db):
    _ticks([23400 + 0.5 * i for i in range(400)])
    spots = [23500 + 5 * i for i in range(60)]
    _day(spots, start="09:15", expiry="2026-09-24")  # near: expiry day
    _scale_near(0.3)  # ATM ~₹18 < ₹40: thin
    _day(spots, start="09:15", expiry="2026-10-01")  # next week, full premium
    near = strategy_lab.run_session("pa_structure_spread", "NIFTY", SESSION)
    switched = strategy_lab.run_session("pa_structure_next_spread", "NIFTY", SESSION)
    assert near and all(t["expiry"] == "near" for t in near)
    assert switched and switched[0]["expiry"] == "next"
    assert switched[0]["basis_points"] > near[0]["basis_points"]  # more credit on next week


def test_thin_premium_without_next_quotes_means_no_trade(db):
    _ticks([23400 + 0.5 * i for i in range(400)])
    _day([23500 + 5 * i for i in range(60)], start="09:15", expiry="2026-09-24")
    _scale_near(0.3)
    assert strategy_lab.run_session("pa_structure_next_spread", "NIFTY", SESSION) == []


def test_normal_premium_stays_on_near_expiry(db):
    _ticks([23400 + 0.5 * i for i in range(400)])
    spots = [23500 + 5 * i for i in range(60)]
    _day(spots, start="09:15", expiry="2026-09-24")
    _day(spots, start="09:15", expiry="2026-10-01")
    got = strategy_lab.run_session("pa_structure_next_spread", "NIFTY", SESSION)
    assert got and got[0]["expiry"] == "near"


def test_candles_use_exchange_time_not_late_arrival(db):
    """A tick that arrives 7 minutes late (2026-09-16) must land in the candle
    of when it traded, not when it reached us."""
    _ticks([23400.0] * 40)  # 09:15-09:35, flat
    late_trade = datetime.fromisoformat(f"{SESSION}T09:16:00+05:30")
    with market_log.connect() as con:
        con.execute(
            "INSERT INTO ticks (ts, session, instrument, security_id, kind, ltp, ltt)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                (late_trade + timedelta(minutes=7)).isoformat(),
                SESSION,
                "NIFTY",
                13,
                "ticker",
                23500.0,
                calendar.timegm(late_trade.timetuple()),
            ),
        )
    bars = strategy_lab._bars_5m("NIFTY", SESSION)
    assert bars["high"].iloc[0] == 23500.0  # 09:15 candle, when it traded
    assert bars["high"].iloc[1] < 23500.0  # not the 09:20 candle it arrived in


def _candles(ohlc):
    import pandas as pd

    return pd.DataFrame(ohlc, columns=["open", "high", "low", "close"])


def test_pullback_buy_waits_for_the_dip_then_the_resume():
    dip_then_resume = _candles([(100, 106, 99, 105), (105, 106, 101, 102), (102, 109, 101, 108)])
    assert strategy_lab._pullback_read("UP", dip_then_resume) == 1
    chasing = _candles([(100, 106, 99, 105), (105, 111, 104, 110), (110, 116, 109, 115)])
    assert strategy_lab._pullback_read("UP", chasing) == 0  # no dip: that's chasing
    no_resume = _candles([(100, 106, 99, 105), (105, 106, 101, 102), (102, 105, 100, 104)])
    assert strategy_lab._pullback_read("UP", no_resume) == 0  # didn't clear the dip high
    assert strategy_lab._pullback_read("RANGE", dip_then_resume) == 0
    mirrored = _candles([(110, 111, 104, 105), (105, 109, 104, 108), (108, 109, 100, 101)])
    assert strategy_lab._pullback_read("DOWN", mirrored) == -1


def test_wall_bounce_reads_the_oi_walls():
    snap = [
        {
            "strike": s,
            "opt_type": t,
            "oi": (9e6 if (s, t) in {(23300.0, "PE"), (23700.0, "CE")} else 1e6),
        }
        for s in (23300.0, 23500.0, 23700.0)
        for t in ("CE", "PE")
    ]
    tapped_support = _candles([(23320, 23330, 23290, 23315)])
    assert strategy_lab._wall_bounce_read(snap, tapped_support, {"structure": "RANGE"}) == 1
    assert strategy_lab._wall_bounce_read(snap, tapped_support, {"structure": "DOWN"}) == 0
    tapped_resistance = _candles([(23690, 23710, 23670, 23680)])
    assert strategy_lab._wall_bounce_read(snap, tapped_resistance, {"structure": "RANGE"}) == -1
    broke_through = _candles([(23320, 23330, 23270, 23280)])  # closed below support
    assert strategy_lab._wall_bounce_read(snap, broke_through, {"structure": "RANGE"}) == 0


def test_buy_candidates_also_need_a_65_percent_win_rate(db, monkeypatch):
    _day([23500 + 5 * i for i in range(60)])
    monkeypatch.setattr(strategy_lab, "MIN_TRADES", 1)
    monkeypatch.setattr(strategy_lab, "MIN_DAYS", 1)

    def fake(name, inst, session, *a, **k):  # 3 small losers + 1 big winner = net +, 25% win
        nets = [-100, -100, -100, 1000] if name in ("oi_bias_buy", "oi_bias_spread") else []
        return [
            {
                "strategy": name,
                "instrument": inst,
                "session": session,
                "net": n,
                "gross": n,
                "charges": 0,
                "exited": f"{session}T10:0{i}",
            }
            for i, n in enumerate(nets)
        ]

    monkeypatch.setattr(strategy_lab, "run_session", fake)
    rows = {r["strategy"]: r for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert rows["oi_bias_spread"]["verdict"] == "PASSING"  # selling: net > 0 is enough
    assert rows["oi_bias_buy"]["verdict"] == "DROPPED"  # buying: 25% < 65% bar


def test_live_buy_lane_replays_the_real_buy_signal(db, monkeypatch, tmp_path):
    """D-08/D-09: the on-demand live_buy_lane candidates call the real,
    un-mocked evaluate_buy_signal against a prior-day candle cache + today's
    recorded ticks/chain -- and stay out of the default run() the dashboard
    calls (endpoint cost unchanged)."""
    monkeypatch.setattr(strategy_lab.candle_cache, "CACHE_ROOT", tmp_path / "candles")
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "1")

    prev_dir = tmp_path / "candles" / "NIFTY_1m"
    prev_dir.mkdir(parents=True)
    t0 = datetime.fromisoformat("2026-09-23T09:15:00")
    lines = ["datetime,open,high,low,close,volume"]
    for i in range(375):  # one full prior session, 1m bars, 09:15-15:29
        t = t0 + timedelta(minutes=i)
        px = 23400 + 0.5 * i
        lines.append(f"{t.isoformat()},{px},{px + 1},{px - 1},{px},1000")
    (prev_dir / "2026-09-23.csv").write_text("\n".join(lines), encoding="utf-8")

    _ticks([23400 + 0.5 * i for i in range(400)])
    _day([23500 + 5 * i for i in range(60)], start="09:15")

    baseline = strategy_lab.run_session("live_buy_lane", "NIFTY", SESSION)
    tuned = strategy_lab.run_session("live_buy_lane_tuned", "NIFTY", SESSION)
    assert isinstance(baseline, list)
    assert isinstance(tuned, list)

    default_names = {r["strategy"] for r in strategy_lab.run(["NIFTY"], [SESSION])["rows"]}
    assert "live_buy_lane" not in default_names
    assert "live_buy_lane_tuned" not in default_names


def _write_prev_day(tmp_path, monkeypatch, *, rows=375, start="2026-09-23T09:15:00", px0=23400.0):
    """Point strategy_lab.candle_cache at a scratch cache and write one prior
    session's worth of 1m candles -- the shared prior-day fixture every
    live_buy_lane test needs, so no test reads the real memory/candles."""
    monkeypatch.setattr(strategy_lab.candle_cache, "CACHE_ROOT", tmp_path / "candles")
    monkeypatch.setenv("CANDLE_INTERVAL_MINUTES", "1")
    prev_dir = tmp_path / "candles" / "NIFTY_1m"
    prev_dir.mkdir(parents=True)
    t0 = datetime.fromisoformat(start)
    lines = ["datetime,open,high,low,close,volume"]
    for i in range(rows):
        t = t0 + timedelta(minutes=i)
        px = px0 + 0.5 * i
        lines.append(f"{t.isoformat()},{px},{px + 1},{px - 1},{px},1000")
    (prev_dir / "2026-09-23.csv").write_text("\n".join(lines), encoding="utf-8")


def test_live_buy_lane_missing_prior_day_returns_no_trades(db, monkeypatch, tmp_path):
    """No NIFTY_1m CSV for the prior date -- degrade to no trades, never raise."""
    monkeypatch.setattr(strategy_lab.candle_cache, "CACHE_ROOT", tmp_path / "candles")
    _day([23500 + 5 * i for i in range(60)], start="09:15")
    assert strategy_lab.run_session("live_buy_lane", "NIFTY", SESSION) == []


def test_live_buy_read_no_look_ahead_and_builds_correct_inputs(monkeypatch, tmp_path):
    """The frame handed to evaluate_buy_signal only contains bars that fully
    closed by the snapshot time, and the OI context is built from that
    snapshot's real strikes -- not a candle-range guess."""
    import types

    _write_prev_day(tmp_path, monkeypatch)
    _ticks([23400 + 0.1 * i for i in range(12)], start="09:15", step_s=30)

    snap = [
        {"strike": 23300.0, "opt_type": "PE", "oi": 5e5, "spot": 23400.0},
        {"strike": 23300.0, "opt_type": "CE", "oi": 2e5, "spot": 23400.0},
        {"strike": 23700.0, "opt_type": "CE", "oi": 9e5, "spot": 23400.0},
        {"strike": 23700.0, "opt_type": "PE", "oi": 1e5, "spot": 23400.0},
    ]

    calls = []

    def recorder(frame, prev, regime, *, params, oi):
        calls.append((frame, prev, regime, params, oi))
        return types.SimpleNamespace(action="NO_TRADE")

    monkeypatch.setattr(strategy_lab, "evaluate_buy_signal", recorder)

    ts = f"{SESSION}T09:18:30+05:30"
    result = strategy_lab._live_buy_read(
        "NIFTY", SESSION, ts, snap, {}, strategy_lab.get_strategy_params()
    )
    assert result == 0  # NO_TRADE -> 0
    assert len(calls) == 1
    frame, prev, regime, params, oi = calls[0]
    assert not prev.empty
    assert frame["end"].max() <= pd.Timestamp(ts)
    assert pd.Timestamp(f"{SESSION}T09:19:00+05:30") not in set(frame["end"])
    assert oi.max_put_oi_strike == 23300.0
    assert oi.max_call_oi_strike == 23700.0


def test_live_buy_read_value_error_returns_zero_not_a_crash(monkeypatch, tmp_path):
    """Too few candles early in the session: evaluate_buy_signal's real
    ValueError must degrade to "no trade", not propagate."""
    _write_prev_day(tmp_path, monkeypatch, rows=5)
    _ticks([23400.0, 23401.0], start="09:15", step_s=30)

    def boom(*a, **k):
        raise ValueError("Need at least 23 intraday candles for buy signal.")

    monkeypatch.setattr(strategy_lab, "evaluate_buy_signal", boom)
    snap = [{"strike": 23400.0, "opt_type": "CE", "oi": 1, "spot": 23400.0}]
    ts = f"{SESSION}T09:16:00+05:30"
    result = strategy_lab._live_buy_read(
        "NIFTY", SESSION, ts, snap, {}, strategy_lab.get_strategy_params()
    )
    assert result == 0


def test_live_buy_lane_direction_mapping(db, monkeypatch, tmp_path):
    """BUY_CALL opens a CE leg, BUY_PUT a PE leg, NO_TRADE opens nothing --
    driven by the real run_session/CANDIDATES wiring, evaluate_buy_signal
    mocked only to pick the direction deterministically."""
    import types

    _write_prev_day(tmp_path, monkeypatch)
    _ticks([23400 + 0.5 * i for i in range(400)])
    _day([23500 + 5 * i for i in range(60)], start="09:15")

    monkeypatch.setattr(
        strategy_lab,
        "evaluate_buy_signal",
        lambda *a, **k: types.SimpleNamespace(action="BUY_CALL"),
    )
    calls = strategy_lab.run_session("live_buy_lane", "NIFTY", SESSION)
    assert calls and calls[0]["legs"][0].startswith("BUY") and calls[0]["legs"][0].endswith("CE")

    monkeypatch.setattr(
        strategy_lab, "evaluate_buy_signal", lambda *a, **k: types.SimpleNamespace(action="BUY_PUT")
    )
    puts = strategy_lab.run_session("live_buy_lane", "NIFTY", SESSION)
    assert puts and puts[0]["legs"][0].startswith("BUY") and puts[0]["legs"][0].endswith("PE")

    monkeypatch.setattr(
        strategy_lab,
        "evaluate_buy_signal",
        lambda *a, **k: types.SimpleNamespace(action="NO_TRADE"),
    )
    assert strategy_lab.run_session("live_buy_lane", "NIFTY", SESSION) == []


def test_default_run_never_evaluates_live_buy_candidates(db, monkeypatch):
    """T-01-03: the dashboard's GET /api/strategy-lab calls run() with no
    names -- that path must never touch _live_buy_read (it's lazy and
    ON_DEMAND-excluded), so a crashing replacement must never fire."""
    _day([23500 + 5 * i for i in range(60)], start="09:15")

    def boom(*a, **k):
        raise AssertionError("live_buy_lane must not run in the default endpoint call")

    monkeypatch.setattr(strategy_lab, "_live_buy_read", boom)
    result = strategy_lab.run(["NIFTY"], [SESSION])
    assert result["rows"]


def test_live_buy_wrappers_pass_the_right_params():
    """_live_buy reads today's real settings; _live_buy_tuned overrides only
    LIVE_BUY_TUNED's keys via dataclasses.replace."""
    captured = {}

    def fake_read(params):
        captured["v"] = params.buy_block_contra_cpr
        return 0

    sig = {"live_buy": fake_read}
    strategy_lab._live_buy(sig)
    assert captured["v"] is False
    strategy_lab._live_buy_tuned(sig)
    assert captured["v"] is True
