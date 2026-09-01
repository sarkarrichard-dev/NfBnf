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


if __name__ == "__main__":  # setup helper / self-check
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tok:
        print("TELEGRAM_BOT_TOKEN not set in .env — nothing to do.")
        raise SystemExit(0)
    import httpx

    updates = httpx.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=10).json()
    seen: dict[str, str] = {}
    for u in updates.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            seen[str(chat["id"])] = (
                f"{chat.get('type')} · {chat.get('title') or chat.get('username') or chat.get('first_name')}"
            )
    if seen:
        print("Chats that have messaged this bot (put one id in TELEGRAM_CHAT_ID):")
        for cid, label in seen.items():
            print(f"  {cid}   {label}")
    else:
        print("No chats yet — send the bot a message (or add it to a channel) and re-run.")
    if enabled():
        ok = _post("✅ Algo BNF notifications are wired up.")
        print(f"test message sent: {ok}")
