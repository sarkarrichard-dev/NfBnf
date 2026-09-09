"""Crypto Telegram messages — fresh build (2026-09-09).

Delta-perp entries / exits / day recap, quoted in USD with the ₹ conversion
alongside. Transport, the daemon-thread send and the event-keyed de-dup all
live in ``index_ai.notify``; this module only shapes the crypto messages and
picks stable keys so a stuck-state loop can't re-notify the same close.
"""

from __future__ import annotations

from typing import Any

from index_ai.notify import _GREEN, _RED, _WHITE, _tag, send

_STRAT_TAG = {
    "ny_n_break": "6PM",
    "ichimoku": "Ichimoku",
    "fvg_scalp": "FVG",
    "ema_pivot": "EMA+Pivot",
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


def alert(text: str, *, key: str, window_s: float = 3600.0) -> None:
    """A one-off operational alert (kill switch, reconcile), de-duped per key."""
    send(text, key=f"once:{key}", window_s=window_s)


def opened(pos: dict[str, Any]) -> None:
    side = str(pos.get("side", "")).upper()
    key = f"c-entry:{pos.get('asset')}:{pos.get('strategy')}:{pos.get('entry_time') or pos.get('entry_price')}"
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


def closed(row: dict[str, Any]) -> None:
    p = _f(row.get("pnl_usd"))
    mark = _GREEN if p > 0 else _RED if p < 0 else _WHITE
    sign = "+" if p >= 0 else "−"
    send(
        f"{mark} <b>CRYPTO EXIT</b>{_tag(row.get('mode'))} — "
        f"{row.get('asset')} · {_tagname(row.get('strategy'))}\n"
        f"exit @ {_usd(row.get('exit_price'))} · {sign}{_usd(abs(p))} ({sign}{_inr(row.get('pnl_inr'))})\n"
        f"{row.get('exit_reason') or 'closed'}",
        key=f"c-exit:{row.get('exit_id')}",
    )


def day_summary(day: str, rows: list[dict[str, Any]],
                open_positions: list[dict[str, Any]] | None = None) -> None:
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
        lines.append("⚠️ " + str(len(open_positions)) + " still open: " + " · ".join(
            f"{p.get('asset') or '?'} {p.get('strategy') or ''} {p.get('side') or ''}".strip()
            for p in open_positions
        ))
    send("\n".join(lines), key=f"c-day:{day}")


if __name__ == "__main__":  # self-check — stubs the transport, sends nothing
    out: list[tuple] = []
    send = lambda text, **kw: out.append((text, kw.get("key")))  # noqa: E731

    opened({"strategy": "ema_pivot", "asset": "BTCUSD", "side": "long", "size": 30,
            "entry_price": 63240.0, "margin_total_usd": 82.0, "leverage": 100,
            "stop_price": 61900.0, "mode": "paper", "entry_time": "t0"})
    closed({"strategy": "fvg_scalp", "asset": "ETHUSD", "exit_price": 2489.3,
            "pnl_usd": -1.77, "pnl_inr": -150.2, "exit_reason": "trailing stop",
            "mode": "LIVE", "exit_id": "x1"})
    day_summary("2026-09-09", [{"pnl_usd": 2.68, "pnl_inr": 235.0}],
                [{"asset": "SOLUSD", "strategy": "ema_pivot", "side": "short"}])
    assert [k for _, k in out] == ["c-entry:BTCUSD:ema_pivot:t0", "c-exit:x1", "c-day:2026-09-09"]
    assert "· LIVE" in out[1][0] and "· LIVE" not in out[0][0]
    assert "EMA+Pivot" in out[0][0] and "still open" in out[2][0]
    print("crypto.notify self-check ok — keys + LIVE tag + strategy tags")
