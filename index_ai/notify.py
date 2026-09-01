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

from index_ai.premium_trail import premium_trail_cfg, premium_trail_enabled


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


_OPT = {"CALL": "CE", "CE": "CE", "PUT": "PE", "PE": "PE"}


def _cepe(value: Any) -> str:
    return _OPT.get(str(value or "").upper(), "")


def _rupees(value: Any) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _strike(value: Any) -> str:
    try:
        return f"{int(round(float(value)))}"
    except (TypeError, ValueError):
        return "?"


def _paper(mode: str | None) -> str:
    return "" if str(mode or "").upper().startswith("LIVE") else " · paper"


def _primary_leg(
    option: dict[str, Any], action: str, *, leg_exit_ltps: list[float] | None = None
) -> tuple[str, str, Any, Any, Any]:
    """The leg the trade is really about — the short leg of a spread, else the
    single option. Returns (side, CE/PE, strike, entry_px, exit_px)."""
    legs = option.get("legs") or []
    if legs:
        i = next(
            (
                j
                for j, leg in enumerate(legs)
                if str(leg.get("transaction_type") or "").upper() == "SELL"
            ),
            0,
        )
        leg = legs[i]
        side = "SELL" if str(leg.get("transaction_type") or "").upper() == "SELL" else "BUY"
        exit_px = leg.get("exit_ltp")
        if exit_px is None and leg_exit_ltps and i < len(leg_exit_ltps):
            exit_px = leg_exit_ltps[i]
        return side, _cepe(leg.get("option_type")), leg.get("strike"), leg.get("entry_ltp"), exit_px
    side = "SELL" if str(action or "").upper().startswith("SELL") else "BUY"
    return (
        side,
        _cepe(option.get("option_type")),
        option.get("strike"),
        option.get("entry_ltp"),
        option.get("exit_ltp"),
    )


def _sl_and_target(instrument: str, side: str, entry: Any) -> tuple[float | None, float | None]:
    """Stop-loss price and the price where the trailing-profit rule arms, from
    the premium-trail model (None for an index without measured params)."""
    try:
        e = float(entry)
    except (TypeError, ValueError):
        return None, None
    if not premium_trail_enabled(instrument):
        return None, None
    cfg = premium_trail_cfg(instrument)
    hard, pct = float(cfg["hard_stop_pts"]), float(cfg["first_target_pct"])
    if side == "SELL":  # short — loss as the premium rises, profit as it falls
        return e + hard, e * (1 - pct)
    # long — a hard stop wider than the premium just means the whole premium is
    # at risk (the option can only fall to zero), so floor the shown level at 0
    return max(0.0, e - hard), e * (1 + pct)


def trade_opened(*, instrument: str, action: str, mode: str | None, option: dict[str, Any]) -> None:
    side, cepe, strike, entry, _ = _primary_leg(option, action)
    lines = [
        f"\U0001f7e2 <b>ENTRY</b>{_paper(mode)} — {instrument}",
        f"{side} {cepe} {_strike(strike)} @ {_rupees(entry)}",
    ]
    sl, tgt = _sl_and_target(instrument, side, entry)
    if sl is not None:
        lines.append(f"SL {_rupees(sl)} · trailing profit at {_rupees(tgt)}")
    send("\n".join(lines))


def trade_closed(
    *,
    instrument: str,
    action: str,
    mode: str | None,
    option: dict[str, Any],
    pnl: Any,
    reason: str | None = None,
    exit_premium: Any = None,
    leg_exit_ltps: list[float] | None = None,
) -> None:
    from index_ai.day_review import _bucket_exit

    side, cepe, strike, _, exit_px = _primary_leg(option, action, leg_exit_ltps=leg_exit_ltps)
    if exit_px is None:
        exit_px = exit_premium
    try:
        p = float(pnl)
    except (TypeError, ValueError):
        p = 0.0
    mark = "\U0001f7e2" if p > 0 else "\U0001f534" if p < 0 else "⚪"
    word = "Profit" if p > 0 else "Loss" if p < 0 else "Flat"
    send(
        f"{mark} <b>EXIT</b>{_paper(mode)} — {instrument}\n"
        f"{cepe} {_strike(strike)} exit @ {_rupees(exit_px)}\n"
        f"{word} {'+' if p >= 0 else '−'}₹{abs(p):,.0f} · {_bucket_exit(reason)}"
    )


def day_summary(summary: dict[str, Any] | None) -> None:
    if not summary or not summary.get("closed"):
        return
    net = float(summary.get("net_rupees") or 0)
    wr = ""
    if summary.get("win_rate") is not None:
        wr = f" ({float(summary['win_rate']) * 100:.0f}%)"
    lines = [
        f"\U0001f4ca <b>DAY SUMMARY</b> — {summary.get('date', '')}",
        f"{summary.get('closed')} trades · {summary.get('wins', 0)}W / {summary.get('losses', 0)}L{wr}",
        f"Net {'+' if net >= 0 else '−'}₹{abs(net):,.0f}",
    ]
    by = summary.get("by_instrument") or {}
    if by:
        lines.append(
            " · ".join(
                f"{k} {'+' if (v.get('net_rupees') or 0) >= 0 else '−'}₹{abs(v.get('net_rupees') or 0):,.0f}"
                for k, v in by.items()
            )
        )
    ends = summary.get("how_trades_ended") or {}
    if ends:
        lines.append("Exits: " + " · ".join(f"{k} {v}" for k, v in ends.items()))
    send("\n".join(lines))


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
