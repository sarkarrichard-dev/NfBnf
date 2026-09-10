"""notify.py — event-keyed de-dup, the LIVE tag, and the message shapers.

The transport is never hit: every test stubs ``notify._post`` and asserts on the
(text, key, window) it would have sent.
"""

from __future__ import annotations

import pytest

from index_ai import notify


@pytest.fixture
def sent(monkeypatch):
    """Capture what would be posted; force ``enabled()`` on."""
    out: list[tuple[str, str, float]] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    def fake_post(text, key, window_s):
        out.append((text, key, window_s))
        return True

    monkeypatch.setattr(notify, "_post", fake_post)
    return out


def _join(sent):
    return "\n---\n".join(t for t, _, _ in sent)


def test_disabled_is_a_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert not notify.enabled()
    notify.send("hi", key="x")  # must not raise, must not spawn


def test_send_threads_through_to_post(sent):
    notify.send("hello", key="k1", window_s=42.0)
    # daemon thread — give it a beat
    import time

    for _ in range(50):
        if sent:
            break
        time.sleep(0.01)
    assert sent == [("hello", "k1", 42.0)]


def test_trade_opened_and_closed_shapes(sent, monkeypatch):
    monkeypatch.setattr(notify, "_seen", lambda k, w: False)
    notify.trade_opened(
        instrument="BANKNIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        mode="PAPER",
        option={
            "legs": [
                {
                    "transaction_type": "SELL",
                    "option_type": "CE",
                    "strike": 54600,
                    "entry_ltp": 184.0,
                },
                {
                    "transaction_type": "BUY",
                    "option_type": "CE",
                    "strike": 54800,
                    "entry_ltp": 96.0,
                },
            ]
        },
        trade_id="t1",
    )
    notify.trade_closed(
        instrument="BANKNIFTY",
        action="SELL_BEAR_CALL_SPREAD",
        mode="LIVE",
        option={"legs": [{"transaction_type": "SELL", "option_type": "CE", "strike": 54600}]},
        pnl=142.0,
        reason="trailing profit lock",
        exit_premium=41.0,
        trade_id="t1",
    )
    import time

    time.sleep(0.2)
    keys = [k for _, k, _ in sent]
    assert keys == ["entry:t1", "exit:t1"]
    text = _join(sent)
    assert "ENTRY" in text and "SELL CE 54600" in text
    assert "· LIVE" in sent[1][0] and "· LIVE" not in sent[0][0]  # tag only on the live one
    assert "+₹142" in sent[1][0]


def test_dedup_drops_the_second_send_of_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notify, "_stamp_path", lambda: str(tmp_path / "seen.json"))

    import httpx

    calls: list[int] = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: calls.append(1) or type("R", (), {"status_code": 200})(),
    )
    assert notify._post("x", "exit:99", 3600.0) is True
    assert notify._post("x", "exit:99", 3600.0) is False  # same key, inside window → dropped
    assert calls == [1]  # only the first actually hit the API


def test_crypto_shapes_and_keys(sent, monkeypatch):
    monkeypatch.setattr(notify, "_seen", lambda k, w: False)
    notify.crypto_opened(
        {
            "strategy": "ak_roxx_pro",
            "asset": "BTCUSD",
            "side": "long",
            "size": 30,
            "entry_price": 63240.0,
            "margin_total_usd": 82.0,
            "leverage": 100,
            "stop_price": 61900.0,
            "mode": "paper",
            "entry_time": "t0",
        }
    )
    notify.crypto_closed(
        {
            "strategy": "ichimoku",
            "asset": "ETHUSD",
            "exit_price": 2489.3,
            "pnl_usd": -1.77,
            "pnl_inr": -150.2,
            "exit_reason": "trailing stop",
            "mode": "LIVE",
            "exit_id": "x1",
        }
    )
    notify.crypto_day_summary(
        "2026-09-11",
        [{"pnl_usd": 2.68, "pnl_inr": 235.0}],
        [{"asset": "SOLUSD", "strategy": "ak_roxx_pro", "side": "short"}],
    )
    import time

    time.sleep(0.2)
    keys = [k for _, k, _ in sent]
    assert keys == ["c-entry:BTCUSD:ak_roxx_pro:t0", "c-exit:x1", "c-day:2026-09-11"]
    text = _join(sent)
    assert "AKRoxx" in text and "Ichimoku" in text and "still open" in text
    assert "· LIVE" in sent[1][0] and "· LIVE" not in sent[0][0]


def test_day_report_skips_an_empty_day(sent):
    notify.day_report({"date": "2026-09-11", "closed": 0, "open_trades": []})
    import time

    time.sleep(0.1)
    assert sent == []
