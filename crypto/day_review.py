"""Crypto day summary + an advisory AI review — the crypto-side twin of
``index_ai/day_review.py``.

Reads ``crypto_journal.jsonl`` only (no broker call). Produces a per-trade list
(entry / exit / P&L / why in / why out), a summary (record, net USD & INR,
by-strategy, best/worst, how trades ended), and a short narrative + went-right /
went-wrong / watch written by ``index_ai.llm`` (a generic LLM POST wrapper —
infra, not the index brain). Deterministic local fallback when no key is set.

Cached to ``memory/crypto_day_review.json``; refreshed nightly from the crypto
loop and on demand from the dashboard.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from crypto.config import CRYPTO_MEMORY
from crypto.journal import recent

IST = ZoneInfo("Asia/Kolkata")
CACHE_PATH = CRYPTO_MEMORY / "crypto_day_review.json"

_SYSTEM = (
    "You are the analyst for a 24/7 crypto perpetual-futures trading system on "
    "Delta Exchange India (paper mode). Positions run at 100x leverage; the exit "
    "is a P&L-percent trailing stop (initial -10% of margin, ratchets up, "
    "trailing profit from +25%). Exchange fees are ~0.1% of notional per fill = "
    "~10% of the margin on a round trip at 100x, so friction dominates a small "
    "move. Be concrete, tie every point to a trade or a number, say plainly when "
    "the sample is too small to conclude anything, and never predict the market."
)

_REVIEW_PROMPT = """One period of a crypto perp system. Write a short operator review.

Return ONLY a JSON object:
{{
  "narrative": "3-5 sentences: what kind of period it was, net result, what drove it",
  "went_right": ["short concrete points, each tied to a trade or number"],
  "went_wrong": ["short concrete points, each tied to a trade or number"],
  "watch": ["optional: things to check next"]
}}

Note which exits fired — a trade riding to the trailing stop vs the trailing
profit vs a structural signal tells you whether the exit rules are working.
Call out fee drag when a gross-positive trade closed net-negative.

SUMMARY:
{summary}

TRADES:
{trades}

CONTEXT (measured costs, model status):
{context}
"""


def _today_bounds() -> tuple[float, float]:
    now = datetime.now(IST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start.timestamp() + 86_400)


def _ts(row: dict[str, Any]) -> float:
    raw = row.get("closed_at") or row.get("exit_time") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _today_rows() -> list[dict[str, Any]]:
    lo, hi = _today_bounds()
    return [r for r in recent(500) if lo <= _ts(r) < hi]


_EXIT_BUCKETS = (
    ("trailing profit", "trailing profit"),
    ("trailing stop", "trailing stop"),
    ("session end", "session end"),
    ("cloud re-entry", "cloud re-entry"),
    ("supertrend", "trend flip"),
    ("15m", "structural signal"),
    ("N-break", "structural signal"),
    ("inverted-N", "structural signal"),
    ("POC", "reached POC"),
    ("EMA", "EMA cross"),
    ("W break", "structural signal"),
    ("M break", "structural signal"),
    ("exchange", "exchange (bracket/manual)"),
    ("bracket", "exchange (bracket/manual)"),
)


def _bucket_exit(reason: str | None) -> str:
    r = str(reason or "").lower()
    for needle, label in _EXIT_BUCKETS:
        if needle.lower() in r:
            return label
    return "other"


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = len(rows)
    if not closed:
        return {"date": datetime.now(IST).date().isoformat(), "closed": 0,
                "wins": 0, "losses": 0, "net_usd": 0.0, "net_inr": 0.0,
                "how_trades_ended": {}, "by_strategy": {}}
    wins = sum(1 for r in rows if _num(r.get("pnl_usd")) > 0)
    losses = sum(1 for r in rows if _num(r.get("pnl_usd")) < 0)
    ends: dict[str, int] = {}
    for r in rows:
        ends[_bucket_exit(r.get("exit_reason"))] = ends.get(_bucket_exit(r.get("exit_reason")), 0) + 1
    by_strat: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = str(r.get("strategy") or "?")
        b = by_strat.setdefault(k, {"trades": 0, "net_usd": 0.0})
        b["trades"] += 1
        b["net_usd"] = round(b["net_usd"] + _num(r.get("pnl_usd")), 4)
    best = max(rows, key=lambda r: _num(r.get("pnl_usd")))
    worst = min(rows, key=lambda r: _num(r.get("pnl_usd")))
    fee_bleed = sum(
        1 for r in rows
        if _num(r.get("gross_usd")) > 0 and _num(r.get("pnl_usd")) <= 0
    )
    return {
        "date": datetime.now(IST).date().isoformat(),
        "closed": closed, "wins": wins, "losses": losses,
        "win_rate": round(wins / closed, 3),
        "net_usd": round(sum(_num(r.get("pnl_usd")) for r in rows), 4),
        "net_inr": round(sum(_num(r.get("pnl_inr")) for r in rows), 2),
        "how_trades_ended": dict(sorted(ends.items(), key=lambda kv: -kv[1])),
        "by_strategy": by_strat,
        "fee_bled_trades": fee_bleed,
        "best_trade": {"asset": best.get("asset"), "strategy": best.get("strategy"),
                       "pnl_usd": round(_num(best.get("pnl_usd")), 4)},
        "worst_trade": {"asset": worst.get("asset"), "strategy": worst.get("strategy"),
                        "pnl_usd": round(_num(worst.get("pnl_usd")), 4)},
    }


def _trade_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append({
            "asset": r.get("asset"), "strategy": r.get("strategy"), "side": r.get("side"),
            "opened_ist": _to_ist(r.get("entry_time")),
            "closed_ist": _to_ist(r.get("closed_at") or r.get("exit_time")),
            "entry": _num(r.get("entry_price")), "exit": _num(r.get("exit_price")),
            "pnl_usd": round(_num(r.get("pnl_usd")), 4), "pnl_inr": round(_num(r.get("pnl_inr")), 2),
            "peak_pnl_pct": r.get("peak_pnl_pct"),
            "entry_reason": r.get("entry_reason"), "exit_reason": r.get("exit_reason"),
        })
    return out


def _to_ist(raw: Any) -> str:
    try:
        return (
            datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            .astimezone(IST).strftime("%H:%M")
        )
    except (ValueError, TypeError):
        return "—"


def _context() -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    try:
        from crypto import charges

        ctx["round_trip_cost_usd_at_1k_notional"] = round(charges.fee_usd(1000) * 2, 3)
        ctx["measured_half_spread_bps"] = {
            s: charges.measured_half_spread_bps(s)
            for s in ("BTCUSD", "ETHUSD", "SOLUSD")
        }
    except Exception:
        pass
    try:
        from crypto.ml.model import status as ml_status

        ctx["model"] = ml_status()
    except Exception:
        pass
    try:
        from crypto.ml.optimize import status as tune_status

        ctx["tuning"] = tune_status()
    except Exception:
        pass
    return ctx


def _local_review(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    if not summary.get("closed"):
        return {"narrative": "No crypto trades closed today.", "went_right": [],
                "went_wrong": [], "watch": []}
    net = summary["net_usd"]
    ends = summary.get("how_trades_ended") or {}
    right, wrong = [], []
    if summary["best_trade"]["pnl_usd"] > 0:
        b = summary["best_trade"]
        right.append(f"Best: {b['asset']} {b['strategy']} +${b['pnl_usd']:.2f}.")
    if summary["worst_trade"]["pnl_usd"] < 0:
        w = summary["worst_trade"]
        wrong.append(f"Worst: {w['asset']} {w['strategy']} ${w['pnl_usd']:.2f}.")
    tp = ends.get("trailing profit", 0)
    if tp:
        right.append(f"{tp} trade(s) hit the trailing profit as designed.")
    ts = ends.get("trailing stop", 0)
    if ts and ts >= summary["closed"] / 2:
        wrong.append(f"{ts}/{summary['closed']} trades stopped out on the trailing stop.")
    if summary.get("fee_bled_trades"):
        wrong.append(f"{summary['fee_bled_trades']} gross-positive trade(s) closed net-negative — fee drag.")
    for k, a in (summary.get("by_strategy") or {}).items():
        (right if a["net_usd"] >= 0 else wrong).append(
            f"{k}: {a['trades']} trades, ${a['net_usd']:.2f}."
        )
    watch = []
    if summary["closed"] < 5:
        watch.append("Sample is tiny (<5 trades) — nothing here is conclusive.")
    return {
        "narrative": (
            f"{summary['closed']} trades closed, {summary['wins']}W/{summary['losses']}L, "
            f"net ${net:.2f} (₹{summary['net_inr']:.0f}). Exits: "
            + ", ".join(f"{k} {v}" for k, v in ends.items()) + "."
        ),
        "went_right": right, "went_wrong": wrong, "watch": watch,
    }


def _ai_review(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    from index_ai.llm import ask, enabled

    fallback = _local_review(rows, summary)
    if not enabled() or not rows:
        return {**fallback, "source": "local"}
    prompt = _REVIEW_PROMPT.format(
        summary=json.dumps(summary, default=str)[:3000],
        trades=json.dumps(_trade_cards(rows), default=str)[:9000],
        context=json.dumps(_context(), default=str)[:4000],
    )
    text = ask(_SYSTEM, prompt, max_tokens=1100)
    if not text:
        return {**fallback, "source": "local"}
    try:
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        return {
            "narrative": str(parsed.get("narrative") or fallback["narrative"]),
            "went_right": [str(x) for x in (parsed.get("went_right") or [])][:6],
            "went_wrong": [str(x) for x in (parsed.get("went_wrong") or [])][:6],
            "watch": [str(x) for x in (parsed.get("watch") or [])][:4],
            "source": "ai",
        }
    except Exception:
        return {**fallback, "source": "local"}


def _signature(rows: list[dict[str, Any]]) -> str:
    parts = [datetime.now(IST).date().isoformat()]
    for r in rows:
        parts.append(f"{r.get('exit_id')}:{r.get('pnl_usd')}")
    return "|".join(parts)


def build_crypto_review(*, refresh: bool = False) -> dict[str, Any]:
    rows = _today_rows()
    sig = _signature(rows)
    if not refresh:
        cached = latest_crypto_review()
        if cached and cached.get("signature") == sig:
            return cached
    summary = _summary(rows)
    review = _ai_review(rows, summary)
    out = {
        "generated_at_ist": datetime.now(IST).isoformat(timespec="seconds"),
        "advisory_only": True, "signature": sig,
        "summary": summary, "trades": _trade_cards(rows), "review": review,
    }
    try:
        CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return out


def latest_crypto_review() -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


if __name__ == "__main__":  # self-check — real journal, no LLM call forced
    rows = _today_rows()
    s = _summary(rows)
    assert set(s) >= {"net_usd", "wins", "losses", "how_trades_ended", "by_strategy"}
    lr = _local_review(rows, s)
    assert "narrative" in lr and isinstance(lr["went_wrong"], list)
    assert _bucket_exit("trailing profit +76% P&L (peak +78%)") == "trailing profit"
    assert _bucket_exit("15m inverted-N") == "structural signal"
    assert _bucket_exit("something odd") == "other"
    full = build_crypto_review(refresh=True)
    assert full["advisory_only"] and "summary" in full and "review" in full
    print(f"crypto.day_review self-check ok — {s['closed']} closed today, "
          f"net ${s['net_usd']:.2f}, review source={full['review']['source']}")
