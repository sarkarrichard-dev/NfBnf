"""Telegram push — clean-slate rebuild (2026-09-10, new bot + group).

One ``httpx`` POST to the Bot API ``/sendMessage`` (no SDK). Every notification
carries a stable ``key`` (``exit:<trade_id>``, ``day:<date>`` …) and the same key
never sends twice inside its window — stamped **on disk**, so a restart or a
re-processed exit cannot re-notify. That is what stops the "same EXIT over and
over" class of bug regardless of how many times a lane re-emits it. (The other
half of that bug is always a *second process* on the same token — see
``memory/telegram-notifications.md``.)

No-op unless ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are set. Every send
is fire-and-forget on a daemon thread and every failure is swallowed — a missing
alert must never stall or break a trade.

Both venues live here: ``trade_opened`` / ``trade_closed`` / ``day_report`` /
``pre_open`` for Indian index options, ``crypto_opened`` / ``crypto_closed`` /
``crypto_alert`` / ``crypto_day_summary`` for Delta perps.

Chat-id setup: message the new bot (or add it to the group), then
``python -m index_ai.notify``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_TRADE_WINDOW_S = 6 * 3600.0  # a trade opens / closes once — a repeat inside 6h is a bug
_STAMP_TTL_S = 3 * 86_400.0
_API = "https://api.telegram.org/bot{token}/sendMessage"

_GREEN, _RED, _WHITE = "\U0001f7e2", "\U0001f534", "⚪"


# ── config ──────────────────────────────────────────────────────────────────


def _conf() -> tuple[str, str] | None:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return (tok, chat) if tok and chat else None


def enabled() -> bool:
    return _conf() is not None


# ── restart-safe de-dup ─────────────────────────────────────────────────────


def _stamp_path() -> str:
    from index_ai.config import MEMORY_DIR

    return str(MEMORY_DIR / ".notify_seen.json")


def _seen(key: str, window_s: float) -> bool:
    """True if ``key`` was stamped within ``window_s`` — records it otherwise.
    Any I/O error fails *open* (a possible duplicate beats a missed alert)."""
    now = time.time()
    path = _stamp_path()
    try:
        with open(path, encoding="utf-8") as fh:
            stamps: dict[str, float] = json.load(fh)
    except (OSError, ValueError):
        stamps = {}
    if now - float(stamps.get(key) or 0) < window_s:
        return True
    stamps[key] = now
    stamps = {k: v for k, v in stamps.items() if now - float(v or 0) < _STAMP_TTL_S}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # per-thread temp name — two daemon sends stamping at once must not
        # collide on the same .tmp (os.replace is atomic, so the target is
        # always a whole document from one writer or the other)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stamps, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return False


# ── transport ───────────────────────────────────────────────────────────────


def _post(text: str, key: str, window_s: float) -> bool:
    cfg = _conf()
    if not cfg:
        return False
    if _seen(key, window_s):
        logger.info("Telegram: dropped duplicate key=%s", key)
        return False
    token, chat = cfg
    try:
        import httpx

        resp = httpx.post(
            _API.format(token=token),
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)
        return False
    if resp.status_code == 200:
        return True
    try:
        why = resp.json().get("description") or resp.text[:200]
    except Exception:
        why = f"HTTP {resp.status_code}"
    logger.warning("Telegram send failed: %s | %.100s", why, text)
    return False


def send(text: str, *, key: str | None = None, window_s: float = _TRADE_WINDOW_S) -> None:
    """Fire-and-forget. ``key`` de-dups the event (defaults to a hash of the
    text, so an accidental resend is still caught for a short spell)."""
    if not enabled():
        return
    k = key or "text:" + hashlib.sha256(text.encode()).hexdigest()[:16]
    w = window_s if key else 600.0
    threading.Thread(target=_post, args=(text, k, w), daemon=True).start()


def alert(text: str, *, key: str, window_s: float = 3600.0) -> None:
    """A one-off operational alert (kill switch, reconcile, live-order failure),
    de-duped per key so a stuck loop cannot spam it."""
    send(text, key=f"once:{key}", window_s=window_s)


# ── formatting ──────────────────────────────────────────────────────────────

_CEPE = {"CALL": "CE", "CE": "CE", "PUT": "PE", "PE": "PE"}


def _tag(mode: str | None) -> str:
    return " · LIVE" if str(mode or "").upper().startswith("LIVE") else ""


def _rs(v: Any) -> str:
    try:
        return f"₹{float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _rs0(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if n >= 0 else '−'}₹{abs(n):,.0f}"


def _strike(v: Any) -> str:
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return ""


def _leg(option: dict[str, Any], action: str) -> tuple[str, str, Any, Any]:
    """(side, CE/PE, strike, entry_px) for the leg the trade is about — the short
    leg of a spread, else the single option."""
    legs = option.get("legs") or []
    if legs:
        leg = next(
            (x for x in legs if str(x.get("transaction_type") or "").upper() == "SELL"),
            legs[0],
        )
        side = "SELL" if str(leg.get("transaction_type") or "").upper() == "SELL" else "BUY"
        return (
            side,
            _CEPE.get(str(leg.get("option_type") or "").upper(), ""),
            leg.get("strike"),
            leg.get("entry_ltp") or leg.get("ltp"),
        )
    side = "SELL" if str(action or "").upper().startswith("SELL") else "BUY"
    return (
        side,
        _CEPE.get(str(option.get("option_type") or "").upper(), ""),
        option.get("strike"),
        option.get("entry_ltp") or option.get("ltp"),
    )


# ── index messages ──────────────────────────────────────────────────────────


def trade_opened(
    *,
    instrument: str,
    action: str,
    mode: str | None,
    option: dict[str, Any],
    trade_id: str | None = None,
) -> None:
    side, cepe, strike, entry = _leg(option, action)
    struct = str(action or "").replace("_", " ").title()
    lines = [
        f"{_GREEN} <b>ENTRY</b>{_tag(mode)} — {instrument}",
        f"{struct}",
        f"{side} {cepe} {_strike(strike)} @ {_rs(entry)}".rstrip(),
    ]
    sl = option.get("stop_loss") or option.get("hard_stop")
    tgt = option.get("first_target") or option.get("target")
    tail = " · ".join(
        p
        for p in (
            f"SL {_rs(sl)}" if sl else "",
            f"T1 {_rs(tgt)}" if tgt else "",
        )
        if p
    )
    if tail:
        lines.append(tail)
    send("\n".join(lines), key=f"entry:{trade_id}" if trade_id else None)


def trade_closed(
    *,
    instrument: str,
    action: str,
    mode: str | None,
    option: dict[str, Any],
    pnl: Any,
    reason: str | None = None,
    exit_premium: Any = None,
    trade_id: str | None = None,
) -> None:
    from index_ai.day_review import _bucket_exit

    _, cepe, strike, _entry = _leg(option, action)
    try:
        p = float(pnl)
    except (TypeError, ValueError):
        p = 0.0
    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    head = f"{cepe} {_strike(strike)}".strip()
    send(
        f"{mark} <b>EXIT</b>{_tag(mode)} — {instrument}\n"
        f"{head + ' ' if head else ''}exit @ {_rs(exit_premium)} · {_rs0(p)}\n"
        f"{_bucket_exit(reason)}",
        key=f"exit:{trade_id}" if trade_id else None,
    )


def day_report(summary: dict[str, Any] | None) -> None:
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
        f"\U0001f4ca <b>DAY</b> — {summary.get('date', '')}",
        f"{summary.get('closed', 0)} closed · "
        f"{summary.get('wins', 0)}W / {summary.get('losses', 0)}L{wr} · {_rs0(net)}",
    ]
    by = summary.get("by_instrument") or {}
    if by:
        lines.append(" · ".join(f"{k} {_rs0(v.get('net_rupees') or 0)}" for k, v in by.items()))
    if opens:
        lines.append(
            f"⚠️ {len(opens)} still open: "
            + " · ".join(
                f"{o.get('instrument') or '?'} {str(o.get('action') or '').replace('_', ' ')}".strip()
                for o in opens
            )
        )
    send("\n".join(lines), key=f"day:{summary.get('date')}")


def pre_open(brief: dict[str, Any] | None) -> None:
    if not brief:
        return
    from index_ai.market_clock import today_ist_date

    date = today_ist_date()
    lines = [f"\U0001f514 <b>PRE-OPEN</b> — {date}"]
    for key, s in (brief.get("index_snapshots") or {}).items():
        if not isinstance(s, dict) or s.get("error"):
            continue
        action = str(s.get("action") or "NO_TRADE")
        conf = s.get("confidence")
        plan = "no clear entry" if action == "NO_TRADE" else action.replace("_", " ")
        if action != "NO_TRADE" and conf is not None:
            plan += f" {float(conf):.0%}"
        regime = str(s.get("cpr_regime") or "").replace("_", " ").lower()
        lines.append(f"<b>{key}</b> {regime} → {plan}".rstrip())
    if len(lines) == 1:
        return
    send("\n".join(lines), key=f"preopen:{date}")


# ── crypto messages ─────────────────────────────────────────────────────────

_STRAT_TAG = {
    "ny_n_break": "6PM",
    "ichimoku": "Ichimoku",
    "ak_roxx_pro": "AKRoxx",
    "cpr_trend": "CPRTrend",
}


def _tagname(strategy: Any) -> str:
    s = str(strategy or "")
    return _STRAT_TAG.get(s, s)


def _f(v: Any) -> float:
    """Tolerant float — a malformed journal row must never crash the scan loop."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _usd(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _inr(v: Any) -> str:
    try:
        return f"₹{abs(float(v)):,.0f}"
    except (TypeError, ValueError):
        return "—"


def crypto_opened(pos: dict[str, Any]) -> None:
    side = str(pos.get("side", "")).upper()
    key = (
        f"c-entry:{pos.get('asset')}:{pos.get('strategy')}:"
        f"{pos.get('entry_time') or pos.get('entry_price')}"
    )
    line = (
        f"{side} {pos.get('size')} @ {_usd(pos.get('entry_price'))} "
        f"({_usd(pos.get('margin_total_usd'))} margin · {pos.get('leverage')}x)"
    )
    if pos.get("stop_price"):
        line += f" · SL {_usd(pos['stop_price'])}"
    send(
        f"{_GREEN} <b>CRYPTO ENTRY</b>{_tag(pos.get('mode'))} — "
        f"{pos.get('asset')} · {_tagname(pos.get('strategy'))}\n{line}",
        key=key,
    )


def crypto_closed(row: dict[str, Any]) -> None:
    p = _f(row.get("pnl_usd"))
    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    sign = "+" if p >= 0 else "−"
    send(
        f"{mark} <b>CRYPTO EXIT</b>{_tag(row.get('mode'))} — "
        f"{row.get('asset')} · {_tagname(row.get('strategy'))}\n"
        f"exit @ {_usd(row.get('exit_price'))} · "
        f"{sign}{_usd(abs(p))} ({sign}{_inr(row.get('pnl_inr'))})\n"
        f"{row.get('exit_reason') or 'closed'}",
        key=f"c-exit:{row.get('exit_id')}",
    )


# back-compat alias — crypto lanes historically called notify.alert for crypto too
crypto_alert = alert


# ── commodity (MCX) messages ────────────────────────────────────────────────


def commodity_opened(pos: dict[str, Any], label: str, lots: int) -> None:
    side = str(pos.get("dir", "")).upper()
    send(
        f"{_GREEN} <b>COMMODITY ENTRY</b>{_tag(pos.get('mode') or 'PAPER')} — {label}\n"
        f"{side} {lots} lot @ {_inr(pos.get('entry'))} · SL {_inr(pos.get('stop'))}\n"
        f"{pos.get('trend_reason') or ''}".rstrip(),
        key=f"m-entry:{pos.get('instrument') or label}:{pos.get('entry_time')}",
    )


def commodity_closed(row: dict[str, Any]) -> None:
    p = _f(row.get("net_rupees"))
    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    sign = "+" if p >= 0 else "−"
    send(
        f"{mark} <b>COMMODITY EXIT</b>{_tag(row.get('mode') or 'PAPER')} — "
        f"{row.get('label') or row.get('instrument')} {str(row.get('direction') or '').upper()}\n"
        f"exit @ {_inr(row.get('exit'))} · {sign}{_inr(abs(p))} "
        f"(gross {_inr(row.get('gross_rupees'))}, cost {_inr(row.get('friction_rupees'))})\n"
        f"{row.get('exit_reason') or 'closed'}",
        key=f"m-exit:{row.get('instrument')}:{row.get('exit_time')}",
    )


def crypto_day_summary(
    day: str,
    rows: list[dict[str, Any]],
    open_positions: list[dict[str, Any]] | None = None,
) -> None:
    open_positions = open_positions or []
    if not rows and not open_positions:
        return
    lines = [f"\U0001f4ca <b>CRYPTO DAY</b> — {day}"]
    if rows:
        net_usd = sum(_f(r.get("pnl_usd")) for r in rows)
        wins = sum(1 for r in rows if _f(r.get("pnl_usd")) > 0)
        losses = sum(1 for r in rows if _f(r.get("pnl_usd")) < 0)
        sign = "+" if net_usd >= 0 else "−"
        net_inr = sum(_f(r.get("pnl_inr")) for r in rows)
        lines.append(
            f"{len(rows)} closed · {wins}W / {losses}L · "
            f"{sign}{_usd(abs(net_usd))} ({sign}{_inr(net_inr)})"
        )
    else:
        lines.append("No closed trades.")
    if open_positions:
        lines.append(
            "⚠️ "
            + str(len(open_positions))
            + " still open: "
            + " · ".join(
                f"{p.get('asset') or '?'} {p.get('strategy') or ''} {p.get('side') or ''}".strip()
                for p in open_positions
            )
        )
    send("\n".join(lines), key=f"c-day:{day}")


# ── chat-id setup helper ────────────────────────────────────────────────────


def _chats_from_updates(result: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            c = o.get("chat")
            if isinstance(c, dict) and c.get("id") is not None:
                name = c.get("title") or c.get("username") or c.get("first_name") or ""
                out[str(c["id"])] = f"{c.get('type')} / {name}"
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(result)
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv

    from index_ai.config import ENV_PATH

    load_dotenv(ENV_PATH, override=True)
    _tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not _tok:
        print("TELEGRAM_BOT_TOKEN not set — nothing to do.")
        raise SystemExit(0)
    import httpx

    me = httpx.get(f"https://api.telegram.org/bot{_tok}/getMe", timeout=10).json()
    if not me.get("ok"):
        print(f"token rejected: {me.get('description')}")
        raise SystemExit(1)
    print(f"bot: @{me['result'].get('username')}")
    upd = httpx.get(
        f"https://api.telegram.org/bot{_tok}/getUpdates",
        params={"allowed_updates": '["message","channel_post","my_chat_member"]', "timeout": 0},
        timeout=15,
    ).json()
    seen = _chats_from_updates(upd.get("result", []))
    if seen:
        print("\nChats the bot can see (put one id in TELEGRAM_CHAT_ID):")
        for cid, label in seen.items():
            print(f"  {cid}   {label}")
    else:
        print("\nNo chats. Send the bot a message (or /start@<bot> in a group) and re-run.")
    if enabled():
        ok = _post(
            "QuantHawk notifications online.",
            key=f"selftest:{int(time.time())}",
            window_s=1,
        )
        print(f"\ntest sent: {ok}")
