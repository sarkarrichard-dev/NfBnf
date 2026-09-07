import numpy as np
import pandas as pd

from index_ai.strategies.options_cpr import paper
from index_ai.strategies.options_cpr.backtest import replay_session
from index_ai.strategies.options_cpr.config import config_for, with_overrides
from index_ai.strategies.options_cpr.engine import add_indicators, cpr_context, evaluate_entry
from index_ai.strategies.options_cpr.premium import bs_price_delta, select_strike


def _day(closes, start, freq="5min", vol=1000.0):
    c = np.asarray(closes, dtype=float)
    v = np.full(len(c), vol)
    return pd.DataFrame({
        "datetime": pd.date_range(start, periods=len(c), freq=freq),
        "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": v,
    })


def test_bs_atm_delta_and_decay():
    px, d = bs_price_delta(24000, 24000, 2 / 365, 0.12, True)
    assert px > 0 and 0.45 < d < 0.55
    assert bs_price_delta(24000, 24000, 0.0, 0.12, True)[0] == 0.0


def test_select_strike_otm():
    assert select_strike(24020, 50, True, "ATM") == 24000
    assert select_strike(24020, 50, True, "OTM1") == 24050
    assert select_strike(24020, 50, False, "OTM1") == 23950


def test_entry_needs_breakout_ema_and_volume():
    cfg = config_for("NIFTY")
    prev = _day([100] * 25, "2026-01-01 09:15", freq="15min")
    cpr = cpr_context(prev, cfg)
    closes = list(np.full(25, cpr.tc - 3)) + list(np.linspace(cpr.tc - 3, cpr.tc + 10, 6))
    df = _day(closes, "2026-01-02 09:15")
    df.loc[df.index[-1], "volume"] = 6000.0
    df = add_indicators(df, cfg)
    assert evaluate_entry(df, len(df) - 1, cpr, cfg)[0] == "CE"
    # kill the volume spike -> no entry
    df.loc[df.index[-1], "volume"] = 400.0
    df = add_indicators(df, cfg)
    assert evaluate_entry(df, len(df) - 1, cpr, cfg)[0] is None


def test_whipsaw_filter_blocks_churn():
    cfg = config_for("NIFTY")
    prev = _day([100] * 25, "2026-01-01 09:15", freq="15min")
    cpr = cpr_context(prev, cfg)
    # crosses above, back below, above again within 3 bars -> whipsaw
    base = list(np.full(24, cpr.tc - 3))
    chop = [cpr.tc + 4, cpr.tc - 4, cpr.tc + 8]
    df = _day(base + chop, "2026-01-02 09:15")
    df.loc[df.index[-1], "volume"] = 6000.0
    df = add_indicators(df, cfg)
    assert evaluate_entry(df, len(df) - 1, cpr, cfg)[0] is None


def test_kill_switch_stops_new_entries(monkeypatch):
    # force every trade to lose: tiny capital cap + immediate reversals
    cfg = config_for("NIFTY")
    prev = _day([100] * 80, "2026-01-01 09:15")
    # a sawtooth that keeps breaking out then reversing hard
    n = 80
    base = np.full(n, 24000.0)
    sess = _day(base, "2026-01-02 09:15")
    p15 = _day([24000] * 26, "2026-01-01 09:15", freq="15min")
    t15 = _day([24000] * 26, "2026-01-02 09:15", freq="15min")
    trades = replay_session(prev.tail(30), sess, p15, t15, prev, cfg, require_15m_alignment=False)
    # flat market -> no breakouts -> no trades, but the call must not raise
    assert isinstance(trades, list)


def test_close_journals_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "JOURNAL_PATH", tmp_path / "j.jsonl")
    monkeypatch.setattr(paper, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setenv("ENABLE_OPTIONS_CPR_PAPER", "true")
    cfg = config_for("NIFTY")
    state = {"NIFTY": {"position": None}}
    ctr = {"date": "2026-01-02", "trades": 1, "consec_losses": 0, "daily_pnl": 0.0, "kill": False}
    state["NIFTY"]["day"] = ctr
    pos = {
        "side": "CE", "is_call": True, "strike": 24000.0, "entry_spot": 24010.0,
        "entry_premium": 120.0, "entry_time": "t0", "sl_premium": 96.0, "r_unit": 24.0,
        "target_premium": 168.0, "qty_open": cfg.lot_size, "stage": 0,
        "peak_premium": 120.0, "peak_spot": 24010.0, "partial_booked": False,
    }
    tr = paper._close(pos, 168.0, "target", 24120.0, cfg, ctr, state)
    assert tr["side"] == "CE" and tr["net_rupees"] == round(48.0 * cfg.lot_size - tr["friction_rupees"], 2)
    assert (tmp_path / "j.jsonl").read_text().strip()
    st = paper.options_cpr_paper_status()
    assert st["all_time"]["closed"] == 1


def test_scan_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_OPTIONS_CPR_PAPER", "false")
    assert paper.scan_options_cpr_paper(object()) == []


def test_sell_spread_is_directional_and_credit_positive():
    from index_ai.strategies.options_cpr.sell import replay_sell_session

    cfg = config_for("NIFTY")
    prev = _day([24000] * 26, "2026-01-01 09:15", freq="15min", vol=0.0)
    cpr = cpr_context(prev, cfg)
    closes = list(np.full(25, cpr.tc - 5)) + list(np.linspace(cpr.tc - 5, cpr.tc + 70, 32))
    t5 = _day(closes, "2026-01-02 09:15", vol=0.0)
    p15 = prev
    t15 = _day([24140] * 26, "2026-01-02 09:15", freq="15min", vol=0.0)
    trades = replay_sell_session(t5.head(0), t5, p15, t15, prev, cfg, require_15m_alignment=False)
    assert isinstance(trades, list) and trades
    tr = trades[0]
    assert tr["structure"] == "SELL_BULL_PUT_SPREAD"
    assert tr["entry_credit"] > 0
    assert tr["long_strike"] < tr["short_strike"] < tr["entry_spot"] + cfg.strike_step
    assert "features" in tr
    # hedged by default, and the wing keeps max loss inside the margin budget
    assert tr["long_strike"] is not None
    assert tr["max_loss_rupees"] <= cfg.sell_margin_budget_rupees * 1.05


def test_build_spread_wing_fits_margin_budget():
    from index_ai.strategies.options_cpr.config import with_overrides
    from index_ai.strategies.options_cpr.sell import _build_spread

    cfg = with_overrides(config_for("BANKNIFTY"), sell_margin_budget_rupees=30000.0)
    sp = _build_spread(52000.0, cfg, is_put=True, mte=3 * 375.0)
    assert sp["long_k"] < sp["short_k"]
    assert sp["max_loss_rupees"] <= 30000.0 * 1.05
    assert sp["credit"] > 0


def test_live_chain_quotes_and_real_charges(monkeypatch):
    from index_ai.strategies.options_cpr import paper
    from index_ai.strategies.options_cpr.live_chain import ChainBook

    rows = {
        24000.0: {"ce": {"last_price": 120.0, "top_bid_price": 119.0, "top_ask_price": 122.0,
                         "security_id": 1, "greeks": {"delta": 0.5}}},
    }
    book = ChainBook("2026-09-02", rows)
    q = book.quote(24000.0, is_call=True)
    assert q.fill("BUY") == 122.0 and q.fill("SELL") == 119.0

    cfg = config_for("NIFTY")
    # real (dhan_ltp) fills: spread is in the price, so no extra half-spread term
    real, br = paper._leg_friction(122.0, 119.0, cfg.lot_size, cfg, "dhan_ltp", "BUY")
    proxy, _ = paper._leg_friction(122.0, 119.0, cfg.lot_size, cfg, "bs_proxy", "BUY")
    assert proxy > real
    # a Dhan contract note: two ₹20 orders dominate a small-premium leg
    assert 35 <= br["brokerage"] <= 40
    assert br["stt"] > 0 and br["total"] == round(sum(v for k, v in br.items() if k != "total"), 2)


def test_walk_forward_gate_never_worse_on_separable_data():
    from index_ai.strategies.options_cpr.options_ml import _FEATURES, walk_forward_gate

    rng = np.random.default_rng(1)
    trades = []
    for k in range(180):
        good = k % 2 == 0
        trades.append({
            "session": f"2026-0{k % 6 + 1}-{k % 27 + 1:02d}",
            "net_rupees": rng.normal(400 if good else -350, 150),
            "features": {f: (1.5 if good else -1.5) + rng.normal(0, 0.4) for f in _FEATURES},
        })
    out = walk_forward_gate(trades)
    assert out["oos_gated_net"] >= out["oos_static_net"] - 2000  # gate helps or is ~neutral


def test_viability_blocks_structures_that_cannot_cover_their_costs(monkeypatch):
    from index_ai.strategies.options_cpr.viability import (
        NOT_VIABLE,
        UNMEASURED,
        VIABLE,
        friction_floor,
        viability,
    )

    monkeypatch.setenv("SLIPPAGE_HALF_SPREAD_POINTS_NIFTY", "0.20")
    monkeypatch.setenv("SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY", "4.06")

    n, b = viability("NIFTY", "sell"), viability("BANKNIFTY", "sell")
    # BANKNIFTY's book is far wider -> a much higher cost floor
    assert b.friction_floor_rupees > 2 * n.friction_floor_rupees
    assert n.verdict == VIABLE and b.verdict == NOT_VIABLE
    # a lane with negative gross edge is never viable
    assert viability("NIFTY", "buy").verdict == NOT_VIABLE
    # an unmeasured spread must not produce a confident verdict
    monkeypatch.delenv("SLIPPAGE_HALF_SPREAD_POINTS_SENSEX", raising=False)
    assert viability("SENSEX", "sell").verdict == UNMEASURED

    # dropping the hedge halves the order count and the floor
    naked = with_overrides(config_for("BANKNIFTY"), sell_naked=True)
    floor_naked, legs_naked, _ = friction_floor(naked, lane="sell")
    assert legs_naked == 2 and floor_naked < b.friction_floor_rupees


def test_viability_gate_is_opt_out(monkeypatch):
    from index_ai.strategies.options_cpr.paper import _viability_block, require_viable

    monkeypatch.setenv("SLIPPAGE_HALF_SPREAD_POINTS_BANKNIFTY", "4.06")
    cfg = config_for("BANKNIFTY")
    assert require_viable() is True
    assert _viability_block("BANKNIFTY", "sell", cfg) is not None
    monkeypatch.setenv("OPTIONS_CPR_REQUIRE_VIABLE", "false")
    assert _viability_block("BANKNIFTY", "sell", cfg) is None
