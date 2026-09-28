from datetime import datetime, timezone
from types import SimpleNamespace

from crypto import journal, lanes


def _pos(mode, asset="PAXGUSD"):
    return {
        "strategy": "cpr_trend",
        "asset": asset,
        "side": "short",
        "day": "2026-09-24",
        "mode": mode,
        "entry_price": 4258.2,
        "entry_time": "2026-09-24 11:35:00+00:00",
        "size": 228,
        "contract_value": 0.001,
        "leverage": 20,
        "margin_total_usd": 50.0,
        "notional_usd": 970.9,
        "opened_at": "2026-09-24T11:35:00+00:00",
    }


def test_paper_position_on_removed_gold_coin_is_closed_live_one_is_left(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(
        lanes.market_data, "ticker", lambda sym, client=None: {"mark_price": 4270.0}
    )
    monkeypatch.setattr(lanes.notify, "crypto_closed", lambda row: None)
    st = {
        "cpr_trend:PAXGUSD": {"position": _pos("paper"), "strategy": {}},
        "ny_n_break:XAUTUSD": {"position": _pos("live", "XAUTUSD"), "strategy": {}},
        "cpr_trend:BTCUSD": {"position": None, "strategy": {}},
    }
    events = []
    s = SimpleNamespace(symbols=("BTCUSD", "ETHUSD"))  # gold not in the active set either way
    lanes._prune_removed_coins(st, s, None, 88.0, datetime.now(timezone.utc), events)
    assert "cpr_trend:PAXGUSD" not in st  # paper: closed + dropped
    assert "ny_n_break:XAUTUSD" in st  # live: never closed silently
    assert "cpr_trend:BTCUSD" in st  # allowed coin untouched
    rows = journal.recent()
    assert (
        len(rows) == 1
        and rows[0]["exit_reason"] == "coin removed"
        and rows[0]["asset"] == "PAXGUSD"
    )


def test_paper_position_on_a_coin_just_unticked_from_the_active_set_is_also_closed(
    tmp_path, monkeypatch
):
    """Found 2026-09-28: unticking a coin from CRYPTO_SYMBOLS (the dashboard's
    day-to-day list) alone -- without touching the wider CRYPTO_ALLOWLIST --
    used to leave its open paper position unmanaged forever, since it drops
    out of _symbols_for but the allowlist-only check never noticed."""
    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(lanes.market_data, "ticker", lambda sym, client=None: {"mark_price": 0.25})
    monkeypatch.setattr(lanes.notify, "crypto_closed", lambda row: None)
    st = {"ak_roxx_pro:ADAUSD": {"position": _pos("paper", "ADAUSD"), "strategy": {}}}
    events = []
    # ADAUSD is on the (unchanged) allowlist but no longer in the active symbols
    s = SimpleNamespace(symbols=("BTCUSD", "ETHUSD"))
    lanes._prune_removed_coins(st, s, None, 88.0, datetime.now(timezone.utc), events)
    assert "ak_roxx_pro:ADAUSD" not in st
    rows = journal.recent()
    assert (
        len(rows) == 1 and rows[0]["exit_reason"] == "coin removed" and rows[0]["asset"] == "ADAUSD"
    )
