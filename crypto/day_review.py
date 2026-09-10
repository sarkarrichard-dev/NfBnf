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
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from crypto._util import num as _num
from crypto.config import CRYPTO_MEMORY
from crypto.journal import load_state, recent

IST = ZoneInfo("Asia/Kolkata")
CACHE_PATH = CRYPTO_MEMORY / "crypto_day_review.json"

_SYSTEM = (
    "You are the analyst for a 24/7 crypto perpetual-futures trading system on "
    "Delta Exchange India (paper mode). Positions run at 20x leverage; the exit "
    "is a P&L-percent trailing stop (initial -24% of margin ~ -1.2% price, "
    "ratchets up, trailing profit from +45%). Delta's taker fee plus 18% GST is "
    "about 0.12% of the trade value each way, and the bid-ask spread costs a bit "
    "more — on a small move the charges can be more than the gain. Report grouped "
    "by asset (BTC / ETH / PAX / OTHER), not per trade. Be concrete, tie every "
    "point to a number or an exit pattern, say plainly when the sample is too "
    "small to conclude anything, and never predict the market."
)

_REVIEW_PROMPT = """One period of a crypto perp system. Write a short operator review,
**grouped by asset** — BTC, ETH, PAX (gold) and OTHER (SOL / DOGE / …), not per trade.

Return ONLY a JSON object:
{{
  "narrative": "2-4 sentences: what kind of period it was overall, net result, what drove it",
  "by_group": [
    {{
      "group": "BTC",
      "read": "1-2 sentences: how BTC did — the W/L split, which strategy carried it, which exit dominated",
      "improve": "1-2 sentences: the single most useful change for next time, tied to a number or an exit pattern"
    }}
  ],
  "watch": ["optional: cross-cutting things to check next"]
}}

Only include a group in by_group if it actually traded. For 'improve' be concrete:
e.g. 'every BTC loss exited at the -10% trailing stop with peak +0% — entries are
firing mid-chop; require the 15m trend to hold N bars first' beats 'be more selective'.
Call out fee drag when gross-positive trades closed net-negative.

SUMMARY (numbers per group are already computed — read them, don't recompute):
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


_ASSET_GROUP = {"BTCUSD": "BTC", "ETHUSD": "ETH", "PAXGUSD": "PAX"}


def _asset_group(asset: str | None) -> str:
    """BTC / ETH / PAX / OTHER — the day review is grouped by these, not per
    trade, so 10 BTC trades are one line."""
    return _ASSET_GROUP.get(str(asset or "").upper(), "OTHER")


def _by_asset(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(
            _asset_group(r.get("asset")),
            {"trades": 0, "wins": 0, "losses": 0, "net_usd": 0.0, "net_inr": 0.0,
             "strategies": Counter(), "exits": Counter(), "best_usd": None, "worst_usd": None},
        )
        p = _num(r.get("pnl_usd"))
        g["trades"] += 1
        g["wins"] += 1 if p > 0 else 0
        g["losses"] += 1 if p < 0 else 0
        g["net_usd"] = round(g["net_usd"] + p, 4)
        g["net_inr"] = round(g["net_inr"] + _num(r.get("pnl_inr")), 2)
        g["strategies"][str(r.get("strategy") or "?")] += 1
        g["exits"][_bucket_exit(r.get("exit_reason"))] += 1
        g["best_usd"] = p if g["best_usd"] is None else max(g["best_usd"], p)
        g["worst_usd"] = p if g["worst_usd"] is None else min(g["worst_usd"], p)
    # Counters → plain dicts, ordered by frequency
    for g in groups.values():
        g["strategies"] = dict(g["strategies"].most_common())
        g["exits"] = dict(g["exits"].most_common())
        g["win_rate"] = round(g["wins"] / g["trades"], 3) if g["trades"] else 0.0
    order = ["BTC", "ETH", "PAX", "OTHER"]
    return {k: groups[k] for k in order if k in groups}


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = len(rows)
    if not closed:
        return {"date": datetime.now(IST).date().isoformat(), "closed": 0,
                "wins": 0, "losses": 0, "net_usd": 0.0, "net_inr": 0.0,
                "how_trades_ended": {}, "by_strategy": {}, "by_asset": {}}
    wins = sum(1 for r in rows if _num(r.get("pnl_usd")) > 0)
    losses = sum(1 for r in rows if _num(r.get("pnl_usd")) < 0)
    ends = Counter(_bucket_exit(r.get("exit_reason")) for r in rows)
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
        "how_trades_ended": dict(ends.most_common()),
        "by_strategy": by_strat,
        "by_asset": _by_asset(rows),
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


def _group_improve(g: dict[str, Any]) -> str:
    """A deterministic 'what to improve' for one asset group — the LLM replaces
    this when a key is set, but the local fallback still says something useful."""
    exits, strat = g.get("exits") or {}, g.get("strategies") or {}
    top_exit = next(iter(exits), "")
    stopped = exits.get("trailing stop", 0)
    if g["trades"] >= 3 and g["wins"] == 0 and stopped >= g["trades"] - 1:
        return ("Every trade stopped out with no favourable excursion — entries are "
                "firing mid-chop; wait for the 15m trend to be clearly established.")
    if g["net_usd"] < 0 and top_exit == "trailing stop":
        return ("Losses dominated by the trailing stop. Tighten the entry filter or "
                "widen the initial stop so noise doesn't take the trade before it works.")
    if g["net_usd"] < 0 and g["wins"] >= g["losses"]:
        return "More wins than losses but still net-negative — winners are too small vs fees."
    if g["net_usd"] >= 0:
        return f"Net-positive; keep the {next(iter(strat), 'current')} setup as-is."
    return "Not enough of a pattern yet — keep watching."


def _local_review(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    if not summary.get("closed"):
        return {"narrative": "No crypto trades closed today.", "by_group": [], "watch": []}
    net = summary["net_usd"]
    ends = summary.get("how_trades_ended") or {}
    by_group = []
    for name, g in (summary.get("by_asset") or {}).items():
        strat = ", ".join(f"{k} ×{v}" for k, v in (g.get("strategies") or {}).items())
        top_exit = next(iter(g.get("exits") or {}), "—")
        by_group.append({
            "group": name,
            "read": (
                f"{g['trades']} trades, {g['wins']}W/{g['losses']}L, "
                f"net ${g['net_usd']:.2f} (₹{g['net_inr']:.0f}). {strat}. "
                f"Mostly {top_exit} exits."
            ),
            "improve": _group_improve(g),
        })
    watch = []
    if summary["closed"] < 5:
        watch.append("Sample is tiny (<5 trades) — nothing here is conclusive.")
    if summary.get("fee_bled_trades"):
        watch.append(
            f"{summary['fee_bled_trades']} gross-positive trade(s) closed net-negative — fee drag."
        )
    return {
        "narrative": (
            f"{summary['closed']} trades closed, {summary['wins']}W/{summary['losses']}L, "
            f"net ${net:.2f} (₹{summary['net_inr']:.0f}). Exits: "
            + ", ".join(f"{k} {v}" for k, v in ends.items()) + "."
        ),
        "by_group": by_group, "watch": watch,
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
    text = ask(_SYSTEM, prompt, max_tokens=1200)
    if not text:
        return {**fallback, "source": "local"}
    try:
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        # numbers stay from _summary.by_asset; the LLM only supplies read/improve
        nums = {g["group"]: g for g in fallback["by_group"]}
        merged = []
        for g in parsed.get("by_group") or []:
            name = str(g.get("group") or "").upper()
            base = nums.get(name, {"group": name, "read": "", "improve": ""})
            merged.append({
                "group": name,
                "read": str(g.get("read") or base.get("read") or ""),
                "improve": str(g.get("improve") or base.get("improve") or ""),
            })
        return {
            "narrative": str(parsed.get("narrative") or fallback["narrative"]),
            "by_group": merged or fallback["by_group"],
            "watch": [str(x) for x in (parsed.get("watch") or fallback["watch"])][:4],
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


def open_positions() -> list[dict[str, Any]]:
    """Lane-level open positions right now, across every strategy."""
    return [
        v["position"]
        for k, v in load_state().items()
        if ":" in str(k) and isinstance(v, dict) and v.get("position")
    ]


def send_day_summary() -> dict[str, Any]:
    """Build + cache today's review and push the crypto day recap to Telegram.
    Called at 23:58 IST; anything still open is listed."""
    out = build_crypto_review(refresh=True)
    rows = _today_rows()
    opens = open_positions()
    try:
        from index_ai import notify

        notify.crypto_day_summary(out["summary"].get("date", ""), rows, opens)
    except Exception:
        pass
    return {"date": out["summary"].get("date"), "closed": len(rows), "open": len(opens)}


if __name__ == "__main__":  # self-check — real journal, no LLM call forced
    rows = _today_rows()
    s = _summary(rows)
    assert set(s) >= {"net_usd", "wins", "losses", "how_trades_ended", "by_strategy"}
    lr = _local_review(rows, s)
    assert "narrative" in lr and isinstance(lr["by_group"], list)
    assert _bucket_exit("trailing profit +76% P&L (peak +78%)") == "trailing profit"
    assert _bucket_exit("15m inverted-N") == "structural signal"
    assert _bucket_exit("something odd") == "other"
    full = build_crypto_review(refresh=True)
    assert full["advisory_only"] and "summary" in full and "review" in full
    print(f"crypto.day_review self-check ok — {s['closed']} closed today, "
          f"net ${s['net_usd']:.2f}, review source={full['review']['source']}")
