import json
from datetime import date, timedelta

from index_ai import strategy_performance as sp


def _journal(tmp_path, monkeypatch, rows):
    path = tmp_path / "crypto_journal.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr("crypto.journal.JOURNAL_PATH", path)
    monkeypatch.setattr(sp, "data_epoch", lambda: None)


def _trades(strategy, asset, n, pnl, start=date(2026, 9, 1)):
    return [{"strategy": strategy, "asset": asset, "mode": "paper", "pnl_usd": pnl,
             "day": (start + timedelta(days=i % 16)).isoformat()} for i in range(n)]


def test_only_profitable_coins_of_a_ready_strategy_go_live(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch,
             _trades("cpr_trend", "ETHUSD", 20, 5.0)       # ready strategy, winning coin
             + _trades("cpr_trend", "PAXGUSD", 8, -2.0)    # losing coin
             + _trades("cpr_trend", "SOLUSD", 3, 9.0)      # too few trades on this coin
             + _trades("ny_n_break", "BTCUSD", 10, 4.0))   # strategy not ready (10 trades)
    (ready,) = [r for r in sp.crypto_live_readiness() if r["strategy"] == "cpr_trend"]
    assert ready["ready"] and ready["days_span"] == 16     # real distinct days
    assert sp.crypto_live_pairs() == {("cpr_trend", "ETHUSD")}
    why = {(r["strategy"], r["coin"]): r["why_not"] for r in sp.crypto_live_pair_table()}
    assert why[("cpr_trend", "ETHUSD")] is None
    assert "losing on this coin" in why[("cpr_trend", "PAXGUSD")]
    assert "only 3 trades" in why[("cpr_trend", "SOLUSD")]
    assert "strategy not ready" in why[("ny_n_break", "BTCUSD")]


def test_days_count_every_trading_day_not_first_and_last(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, _trades("ak_roxx_pro", "BTCUSD", 30, 1.0))
    (r,) = sp.crypto_live_readiness()
    assert r["days_span"] == 16


# --- crypto_live_pair_view / GET /api/crypto/live-pairs (UIUX-01) ------------

_CPR = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD")
_ACTIVE = (
    {("cpr_trend", c) for c in _CPR}
    | {("ny_n_break", "BTCUSD"), ("ny_n_break", "ETHUSD"), ("rsi_adx_trend", "BTCUSD")}
)


def _fixture_journal(tmp_path, monkeypatch):
    _journal(
        tmp_path, monkeypatch,
        _trades("cpr_trend", "ETHUSD", 20, 5.0)
        + _trades("cpr_trend", "SOLUSD", 3, 9.0)
        + _trades("cpr_trend", "XRPUSD", 1, 4.0)
        + _trades("cpr_trend", "BNBUSD", 6, -3.0)
        + _trades("cpr_trend", "PAXGUSD", 8, -2.0)
        + _trades("ny_n_break", "BTCUSD", 10, 4.0)
        + _trades("btc_daily_straddle", "BTCUSD", 40, 1.0),
    )


def _by_pair(view):
    return {(p["strategy"], p["coin"]): p for p in view["pairs"]}


def test_view_lists_only_the_pairs_the_lane_visits(tmp_path, monkeypatch):
    _fixture_journal(tmp_path, monkeypatch)
    assert ("btc_daily_straddle", "BTCUSD") in sp.crypto_live_pairs()
    view = sp.crypto_live_pair_view(set(_ACTIVE), True)
    assert set(_by_pair(view)) == _ACTIVE
    assert not any(p["strategy"] == "btc_daily_straddle" or p["coin"] == "PAXGUSD"
                   for p in view["pairs"])


def test_view_live_only_when_armed(tmp_path, monkeypatch):
    _fixture_journal(tmp_path, monkeypatch)
    off = sp.crypto_live_pair_view(set(_ACTIVE), False)
    assert off["armed"] is False and off["read_ok"] is True
    assert not any(p["live"] for p in off["pairs"])
    eth = _by_pair(off)[("cpr_trend", "ETHUSD")]
    assert eth["eligible"] is True and "once crypto is armed" in eth["reason"]

    on = sp.crypto_live_pair_view(set(_ACTIVE), True)
    assert {k for k, p in _by_pair(on).items() if p["live"]} == {("cpr_trend", "ETHUSD")}
    assert _by_pair(on)[("cpr_trend", "ETHUSD")]["reason"] is None
    assert all((p["reason"] is None) == p["live"] for p in on["pairs"])


def test_view_reasons_are_plain_words(tmp_path, monkeypatch):
    _fixture_journal(tmp_path, monkeypatch)
    pairs = _by_pair(sp.crypto_live_pair_view(set(_ACTIVE), True))
    assert "only 3 trades" in pairs[("cpr_trend", "SOLUSD")]["reason"]
    xrp = pairs[("cpr_trend", "XRPUSD")]["reason"]
    assert "only 1 trade on this coin" in xrp and "1 trades" not in xrp
    bnb = pairs[("cpr_trend", "BNBUSD")]["reason"]
    assert "losing on this coin" in bnb and "-$18.00" in bnb
    assert pairs[("cpr_trend", "BTCUSD")]["reason"] == "no trades on this coin yet"
    for coin in ("BTCUSD", "ETHUSD"):
        assert pairs[("ny_n_break", coin)]["reason"].startswith(
            "this strategy needs 30 trades first (10 so far)")
    assert "no paper trades yet" in pairs[("rsi_adx_trend", "BTCUSD")]["reason"]
    for p in pairs.values():
        if p["reason"]:
            assert "None" not in p["reason"] and "$-" not in p["reason"]


def test_view_unreadable_list_keeps_everything_paper(tmp_path, monkeypatch):
    _fixture_journal(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(sp, "crypto_live_pairs", boom)
    view = sp.crypto_live_pair_view(set(_ACTIVE), True)
    assert view["read_ok"] is False
    assert set(_by_pair(view)) == _ACTIVE
    for p in view["pairs"]:
        assert p["live"] is False and p["eligible"] is False
        assert p["trades"] is None and p["net_usd"] is None
        assert "could not be read" in p["reason"]


def test_live_pairs_endpoint_mirrors_the_lane(tmp_path, monkeypatch):
    import dataclasses

    import crypto.config
    import crypto.lanes as lanes
    from fastapi.testclient import TestClient

    from index_ai.server import app

    _fixture_journal(tmp_path, monkeypatch)
    flags = {f.name: False for f in dataclasses.fields(crypto.config.CryptoSettings)
             if f.name.endswith("_enabled")}
    fake = dataclasses.replace(
        crypto.config.crypto_settings(), trading_mode="LIVE", live_armed=True,
        api_key="k", api_secret="s", symbols=("BTCUSD", "ETHUSD", "SOLUSD"),
        **{**flags, "cpr_trend_enabled": True, "ny_nbreak_enabled": True},
    )
    current = {"s": fake}
    monkeypatch.setattr("crypto.config.crypto_settings", lambda: current["s"])
    client = TestClient(app)  # no with-block: never start the app's background loops

    r = client.get("/api/crypto/live-pairs")
    assert r.status_code == 200
    body = r.json()
    want = {(st, sym) for st in lanes._enabled_strategies(fake)
            for sym in lanes._symbols_for(st, fake)}
    assert body["armed"] is True
    assert {(p["strategy"], p["coin"]) for p in body["pairs"]} == want
    assert len(want) == 5 and not any(st == "btc_daily_straddle" for st, _ in want)
    assert {(p["strategy"], p["coin"]) for p in body["pairs"] if p["live"]} == {
        ("cpr_trend", "ETHUSD")}
    assert client.post("/api/crypto/live-pairs").status_code == 405

    current["s"] = dataclasses.replace(fake, live_armed=False)
    body = client.get("/api/crypto/live-pairs").json()
    assert body["armed"] is False and not any(p["live"] for p in body["pairs"])
