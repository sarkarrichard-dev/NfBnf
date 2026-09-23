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
