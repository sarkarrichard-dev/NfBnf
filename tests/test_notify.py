from __future__ import annotations

import index_ai.notify as notify


def test_disabled_without_config(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.enabled() is False
    # must be a silent no-op, not raise, when unconfigured
    notify.send("x")
    notify.trade_opened(instrument="NIFTY", action="BUY_CALL", mode="PAPER", entry_premium=31.9)
    notify.trade_closed(
        instrument="NIFTY",
        action="BUY_CALL",
        mode="PAPER",
        entry_premium=31.9,
        exit_premium=60.25,
        pnl=1842.75,
        opened_at="2026-09-01T10:52:58+05:30",
        closed_at="2026-09-01T11:35:34+05:30",
        reason="Trailing exit",
    )


def test_send_posts_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:  # noqa: D401
            pass

    def _fake_post(url, json, timeout):  # noqa: ANN001
        sent["url"] = url
        sent["json"] = json
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert notify._post("hello") is True
    assert sent["url"].endswith("/bottok/sendMessage")
    assert sent["json"]["chat_id"] == "42" and sent["json"]["text"] == "hello"


def test_formatting_helpers() -> None:
    assert notify._premium(519.3) == "₹519.30"
    assert notify._premium(None) == "—"
    assert notify._clock("2026-09-01T15:13:22+05:30") == "01 Sep 15:13"
    assert (
        notify._held("2026-09-01T12:28:53+05:30", "2026-09-01T15:13:22+05:30") == "  ·  held 2h 44m"
    )
