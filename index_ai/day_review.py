"""End-of-day trade summary + an advisory AI review of how the day went.

Reads the local journal only (no live broker call). Produces:
  - a compact per-trade list (entry, exit, P&L, why it closed)
  - a summary (record, net, by index / lane, best / worst, how trades ended)
  - a short narrative + what went right / what went wrong, written by the LLM
    commentary layer, which is advisory only and never touches execution.

Cached to memory/day_review.json. daily_ops.run_eod() regenerates it after the
close; the dashboard can also force a refresh.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist_iso, today_ist_date

CACHE_PATH = MEMORY_DIR / "day_review.json"
_TS_TAIL = re.compile(r"\s*@\s*\d{1,2}\s+\w{3}\s+\d{4},.*$")  # " @ 31 Aug 2026, 3:14 PM IST"


def _exit_notes() -> dict[str, str]:
    """Latest feedback note per trade — that is where the exit reason is stored."""
    from index_ai.learning import connect

    out: dict[str, str] = {}
    try:
        with connect() as db:
            for r in db.execute(
                "SELECT trade_id, note FROM feedback WHERE note IS NOT NULL ORDER BY id"
            ):
                tid = str(r["trade_id"] or "")
                if tid:
                    out[tid] = _TS_TAIL.sub("", str(r["note"])).strip()
    except Exception:
        pass
    return out


def _today_trades() -> list[dict[str, Any]]:
    from index_ai.learning import format_trade_for_ui, recent_trades

    day = today_ist_date()
    notes = _exit_notes()
    rows: list[dict[str, Any]] = []
    for raw in recent_trades(limit=200):
        if not str(raw.get("created_at") or "").startswith(day):
            continue
        t = format_trade_for_ui(raw)
        rows.append(
            {
                "id": t.get("id"),
                "opened_ist": t.get("created_at_ist"),
                "closed_ist": t.get("closed_at_ist"),
                "instrument": t.get("instrument"),
                "structure": t.get("structure") or t.get("action"),
                "action": t.get("action"),
                "side": t.get("side_label"),
                "lots": t.get("lot_label"),
                "entry_premium": t.get("entry_option_ltp"),
                "exit_premium": t.get("exit_option_ltp"),
                "pnl_rupees": t.get("pnl"),
                "is_open": t.get("is_open"),
                "signal_reason": t.get("signal_reason"),
                "exit_reason": notes.get(str(t.get("id") or "")),
            }
        )
    rows.sort(key=lambda r: str(r.get("opened_ist") or ""))
    return rows


def _lane(row: dict[str, Any]) -> str:
    a = str(row.get("action") or "").upper()
    return "buy" if a.startswith("BUY") else "sell" if a.startswith("SELL") else "other"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("pnl_rupees") is not None and not r.get("is_open")]
    net = round(sum(float(r["pnl_rupees"]) for r in closed), 2)
    wins = [r for r in closed if float(r["pnl_rupees"]) > 0]
    losses = [r for r in closed if float(r["pnl_rupees"]) < 0]

    def _by(keyfn: Any) -> dict[str, dict[str, Any]]:
        agg: dict[str, dict[str, Any]] = {}
        for r in closed:
            k = keyfn(r)
            a = agg.setdefault(k, {"trades": 0, "net_rupees": 0.0})
            a["trades"] += 1
            a["net_rupees"] = round(a["net_rupees"] + float(r["pnl_rupees"]), 2)
        return agg

    ends: dict[str, int] = {}
    for r in closed:
        tag = _bucket_exit(r.get("exit_reason"))
        ends[tag] = ends.get(tag, 0) + 1

    return {
        "date": today_ist_date(),
        "total": len(rows),
        "closed": len(closed),
        "open": len(rows) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "net_rupees": net,
        "gross_win_rupees": round(sum(float(r["pnl_rupees"]) for r in wins), 2),
        "gross_loss_rupees": round(sum(float(r["pnl_rupees"]) for r in losses), 2),
        "by_instrument": _by(lambda r: str(r.get("instrument") or "?")),
        "by_lane": _by(_lane),
        "best_trade": max(closed, key=lambda r: float(r["pnl_rupees"]), default=None),
        "worst_trade": min(closed, key=lambda r: float(r["pnl_rupees"]), default=None),
        "how_trades_ended": dict(sorted(ends.items(), key=lambda x: -x[1])),
    }


def _bucket_exit(reason: str | None) -> str:
    r = str(reason or "").lower()
    if not r:
        return "unknown"
    if "square-off" in r or "square off" in r or "end-of-session" in r:
        return "EOD square-off"
    if "trailing exit" in r or "profit trail" in r:
        return "trailing stop"
    if "hard stop" in r or "stop loss" in r or "max loss" in r:
        return "stop loss"
    if "profit target" in r:
        return "profit target"
    if "supertrend" in r:
        return "supertrend flip"
    if "short put" in r or "short call" in r or "index" in r and "above" in r:
        return "short-strike breach"
    if (
        "regime" in r
        or "opposes" in r
        or "strategy signal" in r
        or ("closing" in r and ("sell" in r or "buy" in r))
        or "ema flip" in r
    ):
        return "signal reversed"
    return "other"


_REVIEW_PROMPT = """Below is one trading day for an Indian index options system (NIFTY + BANKNIFTY,
paper mode). Write a short review for the operator.

Return ONLY a JSON object, no prose around it:
{{
  "narrative": "3-5 sentences: what kind of day it was, net result, what drove it",
  "went_right": ["short concrete points, each tied to a trade or number"],
  "went_wrong": ["short concrete points, each tied to a trade or number"],
  "watch": ["optional: things a careful operator should check tomorrow"]
}}

Ground every point in the data. If the sample is tiny, say so. Do not predict the market.
Note which exits fired (trailing stop vs riding to the 15:10 square-off tells you whether
the exit rules are doing their job).

SUMMARY:
{summary}

TRADES:
{trades}

CONTEXT (measured costs, lane viability, regime):
{context}
"""


def _context() -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    try:
        from index_ai.market_context.spread_calib import status as spread_status

        ctx["spreads"] = spread_status()
    except Exception:
        pass
    try:
        from index_ai.strategies.options_cpr.viability import report as viability_report

        ctx["viability"] = viability_report()
    except Exception:
        pass
    try:
        from index_ai.market_context import context as mkt

        c = mkt.latest() or {}
        ctx["regime"] = {k: c.get(k) for k in ("notes", "conditions", "vix")}
    except Exception:
        pass
    return ctx


def _ai_review(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    from index_ai.brain.commentary import _SYSTEM, enabled

    fallback = _local_review(rows, summary)
    if not enabled() or not rows:
        return {**fallback, "source": "local"}
    try:
        import anthropic

        prompt = _REVIEW_PROMPT.format(
            summary=json.dumps(summary, default=str)[:3000],
            trades=json.dumps(rows, default=str)[:9000],
            context=json.dumps(_context(), default=str)[:4000],
        )
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=os.getenv("AI_COMMENTARY_MODEL", "claude-sonnet-5"),
            max_tokens=1100,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        return {
            "narrative": str(parsed.get("narrative") or fallback["narrative"]),
            "went_right": [str(x) for x in (parsed.get("went_right") or [])][:6],
            "went_wrong": [str(x) for x in (parsed.get("went_wrong") or [])][:6],
            "watch": [str(x) for x in (parsed.get("watch") or [])][:4],
            "source": "claude",
        }
    except Exception:
        return {**fallback, "source": "local"}


def _local_review(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    net = summary.get("net_rupees") or 0
    closed = summary.get("closed") or 0
    if not closed:
        return {
            "narrative": "No trades closed today.",
            "went_right": [],
            "went_wrong": [],
            "watch": [],
        }
    ends = summary.get("how_trades_ended") or {}
    right, wrong = [], []
    best, worst = summary.get("best_trade"), summary.get("worst_trade")
    if best and float(best["pnl_rupees"]) > 0:
        right.append(f"Best: {best['instrument']} {best['structure']} +₹{best['pnl_rupees']:,.0f}.")
    if worst and float(worst["pnl_rupees"]) < 0:
        wrong.append(
            f"Worst: {worst['instrument']} {worst['structure']} ₹{worst['pnl_rupees']:,.0f}."
        )
    eod = ends.get("EOD square-off", 0)
    if eod and eod >= closed / 2:
        wrong.append(
            f"{eod}/{closed} trades rode to the 15:10 square-off — the target/stop rules "
            "are not doing much."
        )
    flip = ends.get("signal reversed", 0)
    if flip and flip >= max(2, closed / 3):
        wrong.append(
            f"{flip}/{closed} trades were closed because the signal flipped — the entry "
            "is whipsawing in and out."
        )
    ts = ends.get("trailing stop", 0)
    if ts:
        right.append(f"{ts} trade(s) exited on the trailing stop as designed.")
    for lane, a in (summary.get("by_lane") or {}).items():
        (right if a["net_rupees"] >= 0 else wrong).append(
            f"{lane} lane: {a['trades']} trades, ₹{a['net_rupees']:,.0f}."
        )
    return {
        "narrative": (
            f"{closed} trades closed, {summary.get('wins')}W/{summary.get('losses')}L, "
            f"net ₹{net:,.0f}. Exits: " + ", ".join(f"{k} {v}" for k, v in ends.items()) + "."
        ),
        "went_right": right,
        "went_wrong": wrong,
        "watch": [],
    }


def build_day_review(*, refresh: bool = False) -> dict[str, Any]:
    if not refresh:
        cached = latest_day_review()
        if cached and cached.get("summary", {}).get("date") == today_ist_date():
            return cached
    rows = _today_trades()
    summary = _summary(rows)
    review = _ai_review(rows, summary)
    out = {
        "generated_at_ist": now_ist_iso(),
        "advisory_only": True,
        "summary": summary,
        "trades": rows,
        "review": review,
    }
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
    return out


def latest_day_review() -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":  # self-check
    rows = _today_trades()
    s = _summary(rows)
    assert set(s) >= {"net_rupees", "wins", "losses", "how_trades_ended", "by_lane"}
    lr = _local_review(rows, s)
    assert "narrative" in lr and isinstance(lr["went_wrong"], list)
    full = build_day_review(refresh=True)
    assert full["advisory_only"] and "summary" in full and "review" in full
    print(
        f"day_review.py self-check ok - {s['closed']} closed today, "
        f"net Rs {s['net_rupees']:,.0f}, review source={full['review']['source']}"
    )
