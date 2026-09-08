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

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from index_ai.premium_trail import premium_trail_cfg, premium_trail_enabled

logger = logging.getLogger(__name__)

# Persistent send-dedup: the same message text inside this window is dropped.
# Survives restarts, so the boot-time re-send storm (a lane re-notifying the
# last exit on every server restart) sends once, not once per restart.
_DEDUP_WINDOW_S = 600.0


def _dedup_path() -> str:
    from index_ai.config import MEMORY_DIR

    return str(MEMORY_DIR / ".notify_dedup.json")


def _dedup_seen(text: str) -> bool:
    """True if this exact text was already sent inside the window. Records it."""
    now = time.time()
    path = _dedup_path()
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    try:
        with open(path, encoding="utf-8") as fh:
            stamps = json.loads(fh.read())
    except (OSError, ValueError):
        stamps = {}
    if now - float(stamps.get(h, 0) or 0) < _DEDUP_WINDOW_S:
        return True
    stamps[h] = now
    stamps = {k: v for k, v in stamps.items() if now - float(v or 0) < 86_400}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(stamps))
        os.replace(tmp, path)
    except OSError:
        pass
    return False


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
    if _dedup_seen(text):
        logger.info("Telegram send skipped (duplicate within %.0fs): %.80s", _DEDUP_WINDOW_S, text)
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
        if resp.status_code != 200:
            # Telegram returns {"ok": false, "description": "..."} — surface it
            # (HTML parse errors, chat-not-found, bot removed) instead of a
            # silent drop. The token is only ever in the URL, never logged here.
            try:
                why = resp.json().get("description") or resp.text[:200]
            except Exception:
                why = f"HTTP {resp.status_code}"
            logger.warning("Telegram send failed: %s | text=%.120s", why, text)
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)
        return False


def send(text: str) -> None:
    """Fire-and-forget. Returns immediately; the POST runs on a daemon thread."""
    if not enabled():
        return
    if os.getenv("NOTIFY_TRACE", "").strip():
        import traceback

        logger.warning(
            "NOTIFY_TRACE | %s\n%s",
            text.splitlines()[0] if text else "",
            "".join(traceback.format_stack()[:-1]),
        )
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
    pivot_label = option.get("pivot_target_label")
    if pivot_label:
        lines.append(f"target ~{pivot_label}")
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


def _lvl(v: Any) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def pre_open(brief: dict[str, Any] | None) -> None:
    """9:20 IST read: the CPR pivot zone, OI lean and planned action per index,
    plus the day's sentiment (India VIX + FII index-futures positioning)."""
    if not brief:
        return
    from index_ai.market_clock import today_ist_date

    lines = [f"\U0001f514 <b>PRE-OPEN</b> — {today_ist_date()}"]
    for key, s in (brief.get("index_snapshots") or {}).items():
        if not isinstance(s, dict) or s.get("error"):
            continue
        cpr = s.get("cpr") or {}
        piv, bc, tc = cpr.get("pivot"), cpr.get("bc"), cpr.get("tc")
        cpr_txt = f"pivot {_lvl(piv)} ({_lvl(bc)}–{_lvl(tc)})" if piv is not None else "pivot —"
        bits = [cpr_txt, str(s.get("cpr_regime") or "—").replace("_", " ").lower()]
        oi = s.get("oi") or {}
        if oi.get("pcr") is not None:
            bits.append(f"PCR {float(oi['pcr']):.2f}")
        if oi.get("bias"):
            bits.append(str(oi["bias"]).replace("_", " "))
        action = str(s.get("action") or "NO_TRADE")
        conf = s.get("confidence")
        plan = action if action != "NO_TRADE" else "no clear entry"
        if action != "NO_TRADE" and conf is not None:
            plan += f" {float(conf):.0%}"
        lines.append(f"<b>{key}</b>  " + " · ".join(bits) + f"\n  → {plan}")

    try:
        from index_ai.market_context import context as mkt

        c = mkt.latest() or {}
    except Exception:
        c = {}
    vix, poi = c.get("vix") or {}, c.get("participant_oi") or {}
    sent = []
    if vix.get("last") is not None:
        chg = f", {float(vix['change_pct']):+.1f}%" if vix.get("change_pct") is not None else ""
        sent.append(f"VIX {float(vix['last']):.1f} ({vix.get('regime', '?')}{chg})")
    fut = poi.get("fii_index_fut_net")
    if fut is not None:
        sent.append(
            f"FII net {'short' if fut < 0 else 'long'} {abs(int(fut)) / 1e5:.1f}L index futures"
        )
    if sent:
        lines.append("\n<b>Sentiment</b>  " + " · ".join(sent))
    lines.append("First entry 9:20 IST.")
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
