from __future__ import annotations

import pytest

import index_ai.notify as notify


@pytest.fixture(autouse=True)
def _isolate_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "_dedup_path", lambda: str(tmp_path / "dedup.json"))


_SPREAD = {
    "legs": [
        {"transaction_type": "BUY", "option_type": "CALL", "strike": 59800, "entry_ltp": 105.25},
        {"transaction_type": "SELL", "option_type": "CALL", "strike": 57600, "entry_ltp": 783.25},
    ]
}
_NAKED = {"option_type": "PUT", "strike": 24000, "entry_ltp": 15.0, "exit_ltp": 29.7}


def test_disabled_without_config(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.enabled() is False
    # silent no-op, not a raise, when unconfigured
    notify.send("x")
    notify.trade_opened(instrument="NIFTY", action="BUY_PUT", mode="PAPER", option=_NAKED)
    notify.trade_closed(
        instrument="NIFTY",
        action="BUY_PUT",
        mode="PAPER",
        option=_NAKED,
        pnl=955.5,
        reason="Trailing exit: x",
    )
    notify.day_summary({"closed": 0})


def test_send_posts_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent: dict[str, object] = {}

    class _Resp:
        status_code = 200

    def _fake_post(url, json, timeout):  # noqa: ANN001
        sent["url"], sent["json"] = url, json
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert notify._post("hello") is True
    assert sent["url"].endswith("/bottok/sendMessage")
    assert sent["json"]["chat_id"] == "42" and sent["json"]["text"] == "hello"

    # the same text again inside the window is dropped, not re-sent
    sent.clear()
    assert notify._post("hello") is False
    assert sent == {}


def test_send_logs_a_failed_telegram_response(monkeypatch, caplog) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class _Resp:
        status_code = 400

        @staticmethod
        def json() -> dict:
            return {"ok": False, "description": "Bad Request: can't parse entities"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    with caplog.at_level("WARNING"):
        assert notify._post("<b>oops") is False
    assert "can't parse entities" in caplog.text


def test_chats_from_updates_finds_group_from_any_update_kind() -> None:
    updates = [
        {
            "update_id": 1,
            "my_chat_member": {
                "chat": {"id": -1001234567890, "type": "supergroup", "title": "Algo BNF alerts"}
            },
        },
        {
            "update_id": 2,
            "message": {"chat": {"id": 55, "type": "private", "first_name": "R"}, "text": "hi"},
        },
    ]
    seen = notify._chats_from_updates(updates)
    assert seen["-1001234567890"].startswith("supergroup · Algo BNF alerts")
    assert "55" in seen


def _capture(monkeypatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(notify, "send", out.append)
    return out


def test_entry_message_short_leg_and_trail_levels(monkeypatch) -> None:
    out = _capture(monkeypatch)
    notify.trade_opened(
        instrument="BANKNIFTY", action="SELL_BEAR_CALL_SPREAD", mode="PAPER", option=_SPREAD
    )
    msg = out[0]
    # short leg of the spread, not the hedge, not the strategy name
    assert "SELL CE 57600 @ ₹783.25" in msg
    assert "BEAR_CALL" not in msg and "59800" not in msg
    # BANKNIFTY hard stop 100 pts, first target a quarter of entry
    assert "SL ₹883.25" in msg
    assert "trailing profit at ₹587.44" in msg


def test_exit_message_uses_realised_pnl_and_bucketed_reason(monkeypatch) -> None:
    out = _capture(monkeypatch)
    notify.trade_closed(
        instrument="BANKNIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        mode="LIVE",
        option=_SPREAD,
        pnl=-3349.5,
        reason="Hard stop: premium moved 130.5 pts against entry 783.2 (>= 100).",
        leg_exit_ltps=[124.35, 914.0],
    )
    msg = out[0]
    assert "CE 57600 exit @ ₹914.00" in msg
    assert "Loss −₹3,350 · stop loss" in msg
    assert "paper" not in msg  # LIVE has no tag


def test_day_summary(monkeypatch) -> None:
    out = _capture(monkeypatch)
    notify.day_summary(
        {
            "date": "2026-09-01",
            "closed": 5,
            "wins": 4,
            "losses": 1,
            "win_rate": 0.8,
            "net_rupees": 2916.5,
            "by_instrument": {
                "NIFTY": {"net_rupees": 2755.0},
                "BANKNIFTY": {"net_rupees": 161.5},
            },
            "how_trades_ended": {"trailing stop": 2, "stop loss": 1},
        }
    )
    msg = out[0]
    assert "5 trades · 4W / 1L (80%)" in msg
    assert "Net +₹2,916" in msg  # rounded, no paise
    assert "NIFTY +₹2,755 · BANKNIFTY +₹162" in msg


def test_pre_open(monkeypatch) -> None:
    out = _capture(monkeypatch)
    monkeypatch.setattr(
        "index_ai.market_context.context.latest",
        lambda: {
            "vix": {"last": 11.81, "regime": "CALM", "change_pct": 2.78},
            "participant_oi": {"fii_index_fut_net": -222032},
        },
    )
    notify.pre_open(
        {
            "index_snapshots": {
                "BANKNIFTY": {
                    "action": "SELL_BEAR_CALL_SPREAD",
                    "confidence": 0.61,
                    "cpr_regime": "TRENDING_BEAR",
                    "cpr": {"pivot": 57310.0, "bc": 56900.0, "tc": 57720.0},
                    "oi": {"pcr": 0.78, "bias": "call_heavy"},
                },
                "NIFTY": {"action": "NO_TRADE", "cpr_regime": "SIDEWAYS", "cpr": {}, "oi": {}},
            }
        }
    )
    msg = out[0]
    assert "PRE-OPEN" in msg
    assert "pivot 57,310 (56,900–57,720) · trending bear · PCR 0.78 · call heavy" in msg
    assert "SELL_BEAR_CALL_SPREAD 61%" in msg
    assert "NIFTY" in msg and "no clear entry" in msg
    assert "VIX 11.8 (CALM, +2.8%)" in msg
    assert "FII net short 2.2L index futures" in msg


def test_entry_stoploss_floored_at_zero_for_cheap_option(monkeypatch) -> None:
    out = _capture(monkeypatch)
    # NIFTY hard stop is 11 pts; a ₹7.45 option can only fall to zero
    notify.trade_opened(
        instrument="NIFTY",
        action="BUY_PUT",
        mode="PAPER",
        option={"option_type": "PUT", "strike": 24000, "entry_ltp": 7.45},
    )
    assert "SL ₹0.00" in out[0] and "-" not in out[0].split("SL")[1].split("·")[0]
