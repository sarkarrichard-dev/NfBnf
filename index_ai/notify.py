"""Optional Telegram push for trade entries and exits.

No-op unless ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are set in ``.env``.
Best effort: the send runs on a daemon thread so it never blocks the scanner
loop, and any failure is swallowed — a missing alert must never stall or break
a trade. One httpx POST, no SDK (httpx is already a dependency).

Get the chat id: message the bot (or add it to a channel/group), then run
``python -m index_ai.notify`` — it prints every chat that has talked to the
bot, and sends a test message if the id is already configured.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from index_ai.market_clock import now_ist_iso, parse_ist_datetime


def _config() -> tuple[str, str] | None:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return (tok, chat) if tok and chat else None


def enabled() -> bool:
    return _config() is not None


def _post(text: str) -> bool:
    cfg = _config()
    if not cfg:
        return False
    tok, chat = cfg
    try:
        import httpx

        resp = httpx.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def send(text: str) -> None:
    """Fire-and-forget. Returns immediately; the POST runs on a daemon thread."""
    if not enabled():
        return
    threading.Thread(target=_post, args=(text,), daemon=True).start()


def _premium(value: Any) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _clock(ts: str | None) -> str:
    dt = parse_ist_datetime(ts)
    return dt.strftime("%d %b %H:%M") if dt else "—"


def _legs_line(legs: list[dict[str, Any]] | None) -> str:
    if not legs:
        return ""
    parts = []
    for leg in legs:
        side = "short" if str(leg.get("transaction_type") or "").upper() == "SELL" else "hedge"
        strike = leg.get("strike")
        opt = str(leg.get("option_type") or "").upper()[:2] or "?"
        parts.append(f"{int(strike) if strike else '?'} {opt} {side}")
    return "\n" + " / ".join(parts)


def _mode_tag(mode: str | None) -> str:
    return "LIVE" if str(mode or "").upper().startswith("LIVE") else "PAPER"


def trade_opened(
    *,
    instrument: str,
    action: str,
    mode: str | None,
    entry_premium: Any,
    index_price: Any = None,
    confidence: Any = None,
    legs: list[dict[str, Any]] | None = None,
) -> None:
    idx = ""
    try:
        idx = f"  ·  index {float(index_price):,.0f}" if index_price is not None else ""
    except (TypeError, ValueError):
        idx = ""
    conf = ""
    try:
        conf = f"  ·  conf {float(confidence):.2f}" if confidence is not None else ""
    except (TypeError, ValueError):
        conf = ""
    send(
        f"\U0001f7e2 <b>ENTRY</b> · {_mode_tag(mode)}\n"
        f"{instrument} {action}\n"
        f"Entry {_premium(entry_premium)}  @ {_clock(now_ist_iso())}{idx}{conf}"
        f"{_legs_line(legs)}"
    )


def _held(opened_at: str | None, closed_at: str | None) -> str:
    a, b = parse_ist_datetime(opened_at), parse_ist_datetime(closed_at)
    if not a or not b:
        return ""
    mins = max(0, int((b - a).total_seconds() // 60))
    h, m = divmod(mins, 60)
    return f"  ·  held {h}h {m:02d}m" if h else f"  ·  held {m}m"


def trade_closed(
    *,
    instrument: str,
    action: str,
    mode: str | None,
    entry_premium: Any,
    exit_premium: Any,
    pnl: Any,
    opened_at: str | None,
    closed_at: str | None,
    reason: str | None = None,
) -> None:
    try:
        pnl_f = float(pnl)
    except (TypeError, ValueError):
        pnl_f = 0.0
    mark = "\U0001f7e2" if pnl_f > 0 else "\U0001f534" if pnl_f < 0 else "⚪"
    sign = "+" if pnl_f >= 0 else "−"
    why = f"\n{reason}" if reason else ""
    send(
        f"{mark} <b>EXIT</b> · {_mode_tag(mode)}  ·  {sign}₹{abs(pnl_f):,.0f}\n"
        f"{instrument} {action}\n"
        f"Entry {_premium(entry_premium)} @ {_clock(opened_at)}  →  "
        f"Exit {_premium(exit_premium)} @ {_clock(closed_at)}"
        f"{_held(opened_at, closed_at)}{why}"
    )


def _chats_from_updates(result: list[dict[str, Any]]) -> dict[str, str]:
    """Every chat seen in any update kind (message, channel_post, my_chat_member,
    service messages from being added to a group, …), found by walking the tree."""
    out: dict[str, str] = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            chat = obj.get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                name = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
                out[str(chat["id"])] = f"{chat.get('type')} · {name}"
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(result)
    return out


if __name__ == "__main__":  # setup helper / self-check
    # run standalone, nothing has loaded .env yet (the server does it at startup)
    from dotenv import load_dotenv

    from index_ai.config import ENV_PATH

    load_dotenv(ENV_PATH, override=True)

    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tok:
        print("TELEGRAM_BOT_TOKEN not set in .env — nothing to do.")
        raise SystemExit(0)
    import httpx

    me = httpx.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=10).json()
    if not me.get("ok"):
        print(f"token rejected by Telegram: {me.get('description')}")
        raise SystemExit(1)
    print(f"bot: @{me['result'].get('username')}")

    data = httpx.get(
        f"https://api.telegram.org/bot{tok}/getUpdates",
        params={"allowed_updates": '["message","channel_post","my_chat_member"]', "timeout": 0},
        timeout=15,
    ).json()
    if not data.get("ok"):
        print(f"getUpdates failed: {data.get('description')}")
        print("(a 409 means a webhook is set — run deleteWebhook first)")
        raise SystemExit(1)

    seen = _chats_from_updates(data.get("result", []))
    if seen:
        print("\nChats this bot can see (put one id in TELEGRAM_CHAT_ID):")
        for cid, label in seen.items():
            print(f"  {cid}   {label}")
        print("\nGroup / supergroup ids are negative — that's expected.")
    else:
        print(
            "\nNo chats found. Telegram only shows a group message to a bot when either\n"
            "  • the bot's group privacy is OFF  (BotFather → /setprivacy → Disable), or\n"
            "  • the message is a command addressed to it.\n"
            "Do this: in the group, send  /start@"
            + str(me["result"].get("username"))
            + "  (or any message after disabling privacy), then re-run this."
        )
    if enabled():
        ok = _post("✅ Algo BNF notifications are wired up.")
        print(f"\ntest message sent: {ok}")
