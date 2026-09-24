from datetime import datetime, timezone

from crypto import journal, lanes


def _pos(mode):
    return {"strategy": "cpr_trend", "asset": "PAXGUSD", "side": "short", "day": "2026-09-24",
            "mode": mode, "entry_price": 4258.2, "entry_time": "2026-09-24 11:35:00+00:00",
            "size": 228, "contract_value": 0.001, "leverage": 20, "margin_total_usd": 50.0,
            "notional_usd": 970.9, "opened_at": "2026-09-24T11:35:00+00:00"}


def test_paper_position_on_removed_gold_coin_is_closed_live_one_is_left(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(lanes.market_data, "ticker", lambda sym, client=None: {"mark_price": 4270.0})
    monkeypatch.setattr(lanes.notify, "crypto_closed", lambda row: None)
    st = {
        "cpr_trend:PAXGUSD": {"position": _pos("paper"), "strategy": {}},
        "ny_n_break:XAUTUSD": {"position": {**_pos("live"), "asset": "XAUTUSD"}, "strategy": {}},
        "cpr_trend:BTCUSD": {"position": None, "strategy": {}},
    }
    events = []
    lanes._prune_removed_coins(st, None, 88.0, datetime.now(timezone.utc), events)
    assert "cpr_trend:PAXGUSD" not in st                      # paper: closed + dropped
    assert "ny_n_break:XAUTUSD" in st                         # live: never closed silently
    assert "cpr_trend:BTCUSD" in st                           # allowed coin untouched
    rows = journal.recent()
    assert len(rows) == 1 and rows[0]["exit_reason"] == "coin removed" and rows[0]["asset"] == "PAXGUSD"
