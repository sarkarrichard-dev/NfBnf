"""Fresh notify — transport, event-keyed de-dup, message shapes."""

from __future__ import annotations

import pytest

import index_ai.notify as notify


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "_stamp_path", lambda: str(tmp_path / "seen.json"))


def test_disabled_is_a_silent_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.enabled() is False
    notify.send("x")
    notify.trade_opened(
        instrument="NIFTY",
        action="BUY_PUT",
        mode="PAPER",
        option={"option_type": "PUT", "strike": 24000, "entry_ltp": 15.0},
    )
    notify.day_report({"closed": 0})


def _wire(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent: list = []

    class _R:
        status_code = 200

    def _post(url, json, timeout):  # noqa: ANN001
        sent.append(json)
        return _R()

    import httpx

    monkeypatch.setattr(httpx, "post", _post)
    return sent


def test_post_sends_then_dedups_on_key(monkeypatch):
    sent = _wire(monkeypatch)
    assert notify._post("hello", "exit:99", 3600.0) is True
    assert sent[0]["chat_id"] == "42" and sent[0]["text"] == "hello"
    # same key inside the window — different text — still dropped
    assert notify._post("hello again", "exit:99", 3600.0) is False
    assert len(sent) == 1


def test_key_dedup_is_on_disk_not_in_memory(monkeypatch):
    # _seen holds no module state — it reads/writes the stamp file every call,
    # so a restart (or a second process on the same box) is de-duped too.
    _wire(monkeypatch)
    assert notify._seen("day:2026-09-09", 3600.0) is False  # first time — records
    assert notify._seen("day:2026-09-09", 3600.0) is True  # persisted to disk
    import json

    with open(notify._stamp_path(), encoding="utf-8") as fh:
        assert "day:2026-09-09" in json.load(fh)


def test_failed_response_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class _R:
        status_code = 400

        @staticmethod
        def json():
            return {"description": "chat not found"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    with caplog.at_level("WARNING"):
        assert notify._post("x", "k", 1.0) is False
    assert "chat not found" in caplog.text


def _capture(monkeypatch):
    out: list = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: out.append((text, kw.get("key"))))
    return out


def test_entry_message_short_leg_and_live_tag(monkeypatch):
    out = _capture(monkeypatch)
    spread = {
        "legs": [
            {
                "transaction_type": "BUY",
                "option_type": "CALL",
                "strike": 59800,
                "entry_ltp": 105.25,
            },
            {
                "transaction_type": "SELL",
                "option_type": "CALL",
                "strike": 57600,
                "entry_ltp": 783.25,
            },
        ]
    }
    notify.trade_opened(
        instrument="BANKNIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        mode="LIVE",
        option=spread,
        trade_id="t7",
    )
    text, key = out[0]
    assert "SELL CE 57600 @ ₹783.25" in text and "· LIVE" in text
    assert "59800" not in text and key == "entry:t7"


def test_exit_message_pnl_reason_and_key(monkeypatch):
    out = _capture(monkeypatch)
    notify.trade_closed(
        instrument="BANKNIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        mode="PAPER",
        option={"option_type": "CALL", "strike": 57600},
        pnl=-3349.5,
        reason="Hard stop: premium moved 130 pts",
        exit_premium=914.0,
        trade_id="t7",
    )
    text, key = out[0]
    assert "−₹3,350" in text and "stop loss" in text and "· LIVE" not in text
    assert key == "exit:t7"


def test_day_report(monkeypatch):
    out = _capture(monkeypatch)
    notify.day_report(
        {
            "date": "2026-09-01",
            "closed": 5,
            "wins": 4,
            "losses": 1,
            "win_rate": 0.8,
            "net_rupees": 2916.5,
            "by_instrument": {"NIFTY": {"net_rupees": 2755.0}, "BANKNIFTY": {"net_rupees": 161.5}},
        }
    )
    text, key = out[0]
    assert "5 closed · 4W / 1L (80%) · +₹2,916" in text
    assert "NIFTY +₹2,755 · BANKNIFTY +₹162" in text
    assert key == "day:2026-09-01"

    out.clear()
    notify.day_report({"date": "x", "closed": 0, "open_trades": []})
    assert out == []


def test_day_report_still_fires_with_open_book(monkeypatch):
    out = _capture(monkeypatch)
    notify.day_report(
        {
            "date": "d",
            "closed": 0,
            "open_trades": [{"instrument": "BANKNIFTY", "action": "SELL_BULL_PUT_SPREAD"}],
        }
    )
    assert "still open" in out[0][0] and "BANKNIFTY" in out[0][0]


def test_chats_from_updates():
    seen = notify._chats_from_updates(
        [
            {"my_chat_member": {"chat": {"id": -100123, "type": "supergroup", "title": "Alerts"}}},
            {"message": {"chat": {"id": 55, "type": "private", "first_name": "R"}}},
        ]
    )
    assert seen["-100123"].startswith("supergroup · Alerts") and "55" in seen
