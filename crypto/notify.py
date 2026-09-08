"""Crypto-flavoured Telegram messages, on top of ``index_ai.notify``.

Trader shorthand, a short strategy tag, no jargon. USD is the native currency;
INR is shown alongside so the wallet impact is legible. The transport, the
daemon-thread send and the text de-dup all live in ``index_ai.notify`` — this
module only adds crypto message shapes and a per-key alert de-dup (so a
restart-triggered kill-switch / reconcile alert fires once, not every boot).
"""

from __future__ import annotations

from typing import Any

from crypto.config import CRYPTO_MEMORY
from index_ai.notify import _seen_recently, mode_tag, send

_TAG = {
    "ny_n_break": "6PM",
    "ichimoku": "Ichimoku",
    "fvg_scalp": "FVG",
    "ema_pivot": "EMA+Pivot",
}

_STAMPS = CRYPTO_MEMORY / "crypto_alert_stamps.json"

_GREEN, _RED, _WHITE = "\U0001f7e2", "\U0001f534", "⚪"


def _tag(strategy: Any) -> str:
    s = str(strategy or "")
    return _TAG.get(s, s)


def _usd(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _inr(v: Any) -> str:
    try:
        return f"₹{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def alert(text: str, *, key: str, gap_s: float = 600.0) -> None:
    """Fire-and-forget Telegram, de-duplicated per ``key`` for ``gap_s`` seconds.
    The stamp is on disk, so a restart doesn't re-send a still-fresh alert."""
    CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
    if not _seen_recently(key, gap_s, str(_STAMPS)):
        send(text)


def opened(pos: dict[str, Any]) -> None:
    side = str(pos.get("side", "")).upper()
    lines = [
        f"{_GREEN} <b>CRYPTO ENTRY</b>{mode_tag(pos.get('mode'))} — "
        f"{pos.get('asset')} · {_tag(pos.get('strategy'))}",
        f"{side} {pos.get('size')} @ {_usd(pos.get('entry_price'))}  "
        f"({_usd(pos.get('margin_total_usd'))} margin · {pos.get('leverage')}x)",
    ]
    if pos.get("stop_price"):
        lines.append(f"SL {_usd(pos['stop_price'])}")
    send("\n".join(lines))


def closed(row: dict[str, Any]) -> None:
    p = float(row.get("pnl_usd") or 0.0)
    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    sign = "+" if p >= 0 else "−"
    send(
        f"{mark} <b>CRYPTO EXIT</b>{mode_tag(row.get('mode'))} — "
        f"{row.get('asset')} · {_tag(row.get('strategy'))}\n"
        f"exit @ {_usd(row.get('exit_price'))}  "
        f"{sign}{_usd(abs(p))} ({sign}{_inr(abs(float(row.get('pnl_inr') or 0)))})\n"
        f"{row.get('exit_reason') or 'closed'}"
    )


def day_summary(
    day: str,
    rows: list[dict[str, Any]],
    open_positions: list[dict[str, Any]] | None = None,
) -> None:
    """Crypto end-of-day recap (23:58 IST). Sends even with nothing closed when
    a position is still open, and lists the open ones."""
    open_positions = open_positions or []
    if not rows and not open_positions:
        return

    lines = [f"\U0001f4ca <b>CRYPTO DAY</b> — {day}"]
    if rows:
        net_usd = sum(float(r.get("pnl_usd") or 0.0) for r in rows)
        net_inr = sum(float(r.get("pnl_inr") or 0.0) for r in rows)
        wins = sum(1 for r in rows if float(r.get("pnl_usd") or 0) > 0)
        losses = sum(1 for r in rows if float(r.get("pnl_usd") or 0) < 0)
        sign = "+" if net_usd >= 0 else "−"
        lines.append(f"{len(rows)} trades · {wins}W / {losses}L")
        lines.append(f"Net {sign}{_usd(abs(net_usd))} ({sign}{_inr(abs(net_inr))})")
    else:
        lines.append("No closed trades.")

    if open_positions:
        lines.append(
            f"⚠️ {len(open_positions)} still open: "
            + " · ".join(
                f"{p.get('asset') or '?'} {p.get('strategy') or ''} "
                f"{p.get('side') or ''} @ {_usd(float(p.get('entry_price') or 0))}".strip()
                for p in open_positions
            )
        )
    send("\n".join(lines))


if __name__ == "__main__":  # self-check — no send unless Telegram is configured
    opened({
        "strategy": "ny_n_break", "asset": "BTCUSD", "side": "long", "size": 4,
        "entry_price": 63240.0, "margin_total_usd": 82.0, "leverage": 3, "stop_price": 61900.0,
    })
    closed({
        "strategy": "ny_n_break", "asset": "BTCUSD", "exit_price": 63910.0,
        "pnl_usd": 2.68, "pnl_inr": 235.0, "exit_reason": "15m inverted-N",
    })
    day_summary("2026-09-07", [{"pnl_usd": 2.68, "pnl_inr": 235.0}])
    day_summary(  # 0 closed but a position still open → still sends
        "2026-09-07", [],
        [{"asset": "BTCUSD", "strategy": "fvg_scalp", "side": "long", "entry_price": 63000.0}],
    )

    # per-key de-dup: second alert inside the window is dropped
    import tempfile
    from pathlib import Path

    _STAMPS = Path(tempfile.mkdtemp()) / "s.json"
    _sent: list[str] = []
    send = _sent.append  # noqa: F811
    alert("x", key="k", gap_s=999)
    alert("x again", key="k", gap_s=999)
    assert _sent == ["x"], _sent
    print("crypto.notify self-check ok (messages only sent if TELEGRAM_* set)")
