"""Crypto-flavoured Telegram messages, built on ``index_ai.notify.send``.

Trader shorthand, no strategy name jargon beyond a short tag. USD is the native
currency; INR is shown alongside so the wallet impact is legible.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from crypto.config import CRYPTO_MEMORY
from index_ai.notify import send

_TAG = {"ny_n_break": "6PM", "ichimoku": "Ichimoku"}

_STAMPS = CRYPTO_MEMORY / "crypto_alert_stamps.json"


def alert(text: str, *, key: str, gap_s: float = 600.0) -> None:
    """Fire-and-forget Telegram, deduped per ``key`` — the stamp is on disk so a
    server restart doesn't re-send a still-fresh alert (reconcile / kill switch
    would otherwise fire on every boot)."""
    now = time.time()
    try:
        stamps = json.loads(_STAMPS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamps = {}
    if now - float(stamps.get(key, 0) or 0) < gap_s:
        return
    stamps[key] = now
    stamps = {k: v for k, v in stamps.items() if now - float(v or 0) < 86_400}
    try:
        CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
        tmp = _STAMPS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stamps), encoding="utf-8")
        os.replace(tmp, _STAMPS)
    except OSError:
        pass
    send(text)


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


def opened(pos: dict[str, Any]) -> None:
    tag = _TAG.get(pos.get("strategy", ""), pos.get("strategy", ""))
    side = str(pos.get("side", "")).upper()
    lines = [
        f"\U0001f7e2 <b>CRYPTO ENTRY</b> · paper — {pos.get('asset')} · {tag}",
        f"{side} {pos.get('size')} @ {_usd(pos.get('entry_price'))}  "
        f"({_usd(pos.get('margin_total_usd'))} margin · {pos.get('leverage')}x)",
    ]
    sl = pos.get("stop_price")
    if sl:
        lines.append(f"SL {_usd(sl)}")
    send("\n".join(lines))


def closed(row: dict[str, Any]) -> None:
    tag = _TAG.get(row.get("strategy", ""), row.get("strategy", ""))
    p = float(row.get("pnl_usd") or 0.0)
    mark = "\U0001f7e2" if p > 0 else "\U0001f534" if p < 0 else "⚪"
    sign = "+" if p >= 0 else "−"
    send(
        f"{mark} <b>CRYPTO EXIT</b> · paper — {row.get('asset')} · {tag}\n"
        f"exit @ {_usd(row.get('exit_price'))}  "
        f"{sign}{_usd(abs(p))} ({sign}{_inr(abs(float(row.get('pnl_inr') or 0)))})\n"
        f"{row.get('exit_reason') or 'closed'}"
    )


def day_summary(day: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    net_usd = sum(float(r.get("pnl_usd") or 0.0) for r in rows)
    net_inr = sum(float(r.get("pnl_inr") or 0.0) for r in rows)
    wins = sum(1 for r in rows if float(r.get("pnl_usd") or 0) > 0)
    losses = sum(1 for r in rows if float(r.get("pnl_usd") or 0) < 0)
    sign = "+" if net_usd >= 0 else "−"
    send(
        f"\U0001f4ca <b>CRYPTO DAY</b> — {day}\n"
        f"{len(rows)} trades · {wins}W / {losses}L\n"
        f"Net {sign}{_usd(abs(net_usd))} ({sign}{_inr(abs(net_inr))})"
    )


if __name__ == "__main__":  # self-check — no send unless Telegram is configured
    opened(
        {
            "strategy": "ny_n_break", "asset": "BTCUSD", "side": "long", "size": 4,
            "entry_price": 63240.0, "margin_total_usd": 82.0, "leverage": 3, "stop_price": 61900.0,
        }
    )
    closed(
        {
            "strategy": "ny_n_break", "asset": "BTCUSD", "exit_price": 63910.0,
            "pnl_usd": 2.68, "pnl_inr": 235.0, "exit_reason": "15m inverted-N",
        }
    )
    day_summary("2026-09-07", [{"pnl_usd": 2.68, "pnl_inr": 235.0}])
    # persistent per-key dedup (rebinds the module globals alert() reads)
    import tempfile
    from pathlib import Path

    _STAMPS = Path(tempfile.mkdtemp()) / "s.json"
    _sent: list[str] = []
    send = _sent.append  # noqa: F811
    alert("x", key="k", gap_s=999)
    alert("x again", key="k", gap_s=999)  # deduped inside the window
    assert _sent == ["x"], _sent
    print("crypto.notify self-check ok (messages only sent if TELEGRAM_* set)")
