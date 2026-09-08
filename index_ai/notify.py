"""Optional Telegram push for trade entries, exits, the day recap and the
pre-open brief.

No-op unless ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are set in ``.env``.
Best effort by design: every send runs on a daemon thread so it can never stall
the scanner loop, and every failure is swallowed and logged — a missing alert
must never break a trade. One ``httpx`` POST, no SDK.

Sends are de-duplicated on the exact message text for a short window, and the
record is on disk, so a server restart (which makes a lane re-emit its last
exit) sends once rather than once per restart.

Chat-id setup: message the bot, then run ``python -m index_ai.notify`` — it
lists every chat the bot can see and sends a test message when the id is set.
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


# ─────────────────────────────  configuration  ──────────────────────────────


def _config() -> tuple[str, str] | None:
    """``(token, chat_id)`` when both are set in the environment, else None."""
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return (tok, chat) if tok and chat else None


def enabled() -> bool:
    return _config() is not None


# ───────────────────────  restart-safe send de-dup  ─────────────────────────

_DEDUP_WINDOW_S = 600.0
_STORE_TTL_S = 86_400  # forget a stamp after a day so the file can't grow forever


def _dedup_path() -> str:
    """Path of the send-dedup stamp file. A function (not a constant) so tests
    can point it at a tmp dir."""
    from index_ai.config import MEMORY_DIR

    return str(MEMORY_DIR / ".notify_dedup.json")


def _seen_recently(key: str, window_s: float, path: str) -> bool:
    """True if ``key`` was stamped in ``path`` within ``window_s``. Records the
    key (and prunes stamps older than a day) as a side effect. Any I/O error
    fails open — a lost stamp means a possible duplicate, never a missed send.
    Shared by :func:`_dedup_seen` here and ``crypto.notify.alert``."""
    now = time.time()
    try:
        with open(path, encoding="utf-8") as fh:
            stamps: dict[str, float] = json.load(fh)
    except (OSError, ValueError):
        stamps = {}

    if now - float(stamps.get(key) or 0) < window_s:
        return True

    stamps[key] = now
    stamps = {k: v for k, v in stamps.items() if now - float(v or 0) < _STORE_TTL_S}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stamps, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return False


def _dedup_seen(text: str) -> bool:
    """True if this exact message text was already sent inside the window."""
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return _seen_recently(key, _DEDUP_WINDOW_S, _dedup_path())


# ────────────────────────────────  transport  ──────────────────────────────

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _post(text: str) -> bool:
    """Send one message synchronously. Returns True only on a 200 from Telegram.
    Drops duplicates and unconfigured sends. Never raises."""
    cfg = _config()
    if not cfg:
        return False
    if _dedup_seen(text):
        logger.info("Telegram send skipped (duplicate within %.0fs): %.80s", _DEDUP_WINDOW_S, text)
        return False

    token, chat = cfg
    try:
        import httpx

        resp = httpx.post(
            _TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as exc:  # network, DNS, timeout — log and move on
        logger.warning("Telegram send error: %s", exc)
        return False

    if resp.status_code == 200:
        return True

    # Surface Telegram's own reason (HTML parse error, chat-not-found, bot
    # kicked) rather than dropping it silently. The token lives only in the URL,
    # which is never logged here.
    try:
        why = resp.json().get("description") or resp.text[:200]
    except Exception:
        why = f"HTTP {resp.status_code}"
    logger.warning("Telegram send failed: %s | text=%.120s", why, text)
    return False


def send(text: str) -> None:
    """Fire-and-forget: returns at once, the POST runs on a daemon thread."""
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


# ──────────────────────────  formatting primitives  ────────────────────────

_GREEN, _RED, _WHITE = "\U0001f7e2", "\U0001f534", "⚪"
_OPT_TYPE = {"CALL": "CE", "CE": "CE", "PUT": "PE", "PE": "PE"}


def mode_tag(mode: str | None) -> str:
    """Marks LIVE orders in a message header; paper (the default) is unmarked."""
    return " · LIVE" if str(mode or "").upper().startswith("LIVE") else ""


def _cepe(value: Any) -> str:
    return _OPT_TYPE.get(str(value or "").upper(), "")


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


def _lvl(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _signed_rupees_0(value: float) -> str:
    """``+₹1,234`` / ``−₹1,234`` — whole rupees, explicit sign, unicode minus."""
    return f"{'+' if value >= 0 else '−'}₹{abs(value):,.0f}"


# ───────────────────────  which leg the trade is about  ─────────────────────


def _primary_leg(
    option: dict[str, Any], action: str, *, leg_exit_ltps: list[float] | None = None
) -> tuple[str, str, Any, Any, Any]:
    """The leg the trade is really about — the short leg of a spread, otherwise
    the single option. Returns ``(side, CE/PE, strike, entry_px, exit_px)``."""
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
    """``(stop_loss_px, first_target_px)`` from the premium-trail model, or
    ``(None, None)`` for an instrument with no measured params."""
    try:
        e = float(entry)
    except (TypeError, ValueError):
        return None, None
    if not premium_trail_enabled(instrument):
        return None, None
    cfg = premium_trail_cfg(instrument)
    hard, pct = float(cfg["hard_stop_pts"]), float(cfg["first_target_pct"])
    if side == "SELL":  # short: loss as the premium rises, profit as it falls
        return e + hard, e * (1 - pct)
    # long: a stop wider than the premium just means the whole premium is at
    # risk (an option floors at zero), so never show a negative stop level
    return max(0.0, e - hard), e * (1 + pct)


# ───────────────────────────  index message builders  ──────────────────────


def trade_opened(*, instrument: str, action: str, mode: str | None, option: dict[str, Any]) -> None:
    side, cepe, strike, entry, _ = _primary_leg(option, action)
    lines = [
        f"{_GREEN} <b>ENTRY</b>{mode_tag(mode)} — {instrument}",
        f"{side} {cepe} {_strike(strike)} @ {_rupees(entry)}",
    ]
    sl, tgt = _sl_and_target(instrument, side, entry)
    if sl is not None:
        lines.append(f"SL {_rupees(sl)} · trailing profit at {_rupees(tgt)}")
    if option.get("pivot_target_label"):
        lines.append(f"target ~{option['pivot_target_label']}")
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

    _, cepe, strike, _, exit_px = _primary_leg(option, action, leg_exit_ltps=leg_exit_ltps)
    if exit_px is None:
        exit_px = exit_premium
    try:
        p = float(pnl)
    except (TypeError, ValueError):
        p = 0.0

    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    word = "Profit" if p > 0 else "Loss" if p < 0 else "Flat"
    send(
        f"{mark} <b>EXIT</b>{mode_tag(mode)} — {instrument}\n"
        f"{cepe} {_strike(strike)} exit @ {_rupees(exit_px)}\n"
        f"{word} {_signed_rupees_0(p)} · {_bucket_exit(reason)}"
    )


def day_summary(summary: dict[str, Any] | None) -> None:
    """The index end-of-day recap. Sends even with nothing closed if a book was
    left open at the bell; stays silent when nothing closed and nothing is open."""
    if not summary:
        return
    opens = summary.get("open_trades") or []
    if not summary.get("closed") and not opens:
        return

    net = float(summary.get("net_rupees") or 0)
    wr = ""
    if summary.get("win_rate") is not None:
        wr = f" ({float(summary['win_rate']) * 100:.0f}%)"

    lines = [
        f"\U0001f4ca <b>DAY SUMMARY</b> — {summary.get('date', '')}",
        f"{summary.get('closed')} trades · {summary.get('wins', 0)}W / {summary.get('losses', 0)}L{wr}",
        f"Net {_signed_rupees_0(net)}",
    ]

    by = summary.get("by_instrument") or {}
    if by:
        lines.append(
            " · ".join(f"{k} {_signed_rupees_0(v.get('net_rupees') or 0)}" for k, v in by.items())
        )
    ends = summary.get("how_trades_ended") or {}
    if ends:
        lines.append("Exits: " + " · ".join(f"{k} {v}" for k, v in ends.items()))
    if opens:
        lines.append(
            f"⚠️ {len(opens)} still open: "
            + " · ".join(
                f"{o.get('instrument') or '?'} {str(o.get('action') or '').replace('_', ' ')}".strip()
                for o in opens
            )
        )
    send("\n".join(lines))


def pre_open(brief: dict[str, Any] | None) -> None:
    """The ~9:20 IST read: per index the CPR zone, OI lean and planned action,
    plus the day's sentiment (India VIX + FII index-futures positioning)."""
    if not brief:
        return
    from index_ai.market_clock import today_ist_date

    lines = [f"\U0001f514 <b>PRE-OPEN</b> — {today_ist_date()}"]
    for key, snap in (brief.get("index_snapshots") or {}).items():
        if not isinstance(snap, dict) or snap.get("error"):
            continue
        cpr = snap.get("cpr") or {}
        piv, bc, tc = cpr.get("pivot"), cpr.get("bc"), cpr.get("tc")
        bits = [
            f"pivot {_lvl(piv)} ({_lvl(bc)}–{_lvl(tc)})" if piv is not None else "pivot —",
            str(snap.get("cpr_regime") or "—").replace("_", " ").lower(),
        ]
        oi = snap.get("oi") or {}
        if oi.get("pcr") is not None:
            bits.append(f"PCR {float(oi['pcr']):.2f}")
        if oi.get("bias"):
            bits.append(str(oi["bias"]).replace("_", " "))

        action = str(snap.get("action") or "NO_TRADE")
        conf = snap.get("confidence")
        plan = "no clear entry" if action == "NO_TRADE" else action
        if action != "NO_TRADE" and conf is not None:
            plan += f" {float(conf):.0%}"
        lines.append(f"<b>{key}</b>  " + " · ".join(bits) + f"\n  → {plan}")

    try:
        from index_ai.market_context import context as mkt

        c = mkt.latest() or {}
    except Exception:
        c = {}
    vix, poi = c.get("vix") or {}, c.get("participant_oi") or {}
    sentiment = []
    if vix.get("last") is not None:
        chg = f", {float(vix['change_pct']):+.1f}%" if vix.get("change_pct") is not None else ""
        sentiment.append(f"VIX {float(vix['last']):.1f} ({vix.get('regime', '?')}{chg})")
    fut = poi.get("fii_index_fut_net")
    if fut is not None:
        sentiment.append(
            f"FII net {'short' if fut < 0 else 'long'} {abs(int(fut)) / 1e5:.1f}L index futures"
        )
    if sentiment:
        lines.append("\n<b>Sentiment</b>  " + " · ".join(sentiment))
    lines.append("First entry 9:20 IST.")
    send("\n".join(lines))


# ────────────────────────────  chat-id setup helper  ───────────────────────


def _chats_from_updates(result: list[dict[str, Any]]) -> dict[str, str]:
    """Every chat seen in any update kind (message, channel_post, my_chat_member,
    a service message from being added to a group…), by walking the JSON tree."""
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
    from dotenv import load_dotenv

    from index_ai.config import ENV_PATH

    load_dotenv(ENV_PATH, override=True)

    _tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not _tok:
        print("TELEGRAM_BOT_TOKEN not set in .env — nothing to do.")
        raise SystemExit(0)

    import httpx

    _me = httpx.get(f"https://api.telegram.org/bot{_tok}/getMe", timeout=10).json()
    if not _me.get("ok"):
        print(f"token rejected by Telegram: {_me.get('description')}")
        raise SystemExit(1)
    _bot = _me["result"].get("username")
    print(f"bot: @{_bot}")

    _data = httpx.get(
        f"https://api.telegram.org/bot{_tok}/getUpdates",
        params={"allowed_updates": '["message","channel_post","my_chat_member"]', "timeout": 0},
        timeout=15,
    ).json()
    if not _data.get("ok"):
        print(f"getUpdates failed: {_data.get('description')}")
        print("(a 409 means a webhook is set — run deleteWebhook first)")
        raise SystemExit(1)

    _seen = _chats_from_updates(_data.get("result", []))
    if _seen:
        print("\nChats this bot can see (put one id in TELEGRAM_CHAT_ID):")
        for _cid, _label in _seen.items():
            print(f"  {_cid}   {_label}")
        print("\nGroup / supergroup ids are negative — that's expected.")
    else:
        print(
            "\nNo chats found. Telegram only shows a group message to a bot when either\n"
            "  - the bot's group privacy is OFF  (BotFather -> /setprivacy -> Disable), or\n"
            "  - the message is a command addressed to it.\n"
            f"In the group, send  /start@{_bot}  (or any message once privacy is off), then re-run."
        )
    if enabled():
        print(f"\ntest message sent: {_post('Algo BNF notifications are wired up.')}")
