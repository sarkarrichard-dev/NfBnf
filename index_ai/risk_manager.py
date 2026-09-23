"""
Account-level risk manager: one daily loss budget across every section that
can lose real money, plus per-strategy size suggestions.

Daily budget (real money only — paper losses cost nothing):
  India live realised P&L + crypto live realised P&L (USD x CRYPTO_USDINR),
  measured against India's existing limit (₹9,000 x lots-per-trade).

  NORMAL      under half the budget lost
  TIGHTENED   half or more lost -> every new India trade is 1 lot
              (tighten, don't stop — Richard's standing preference)
  STOPPED     the whole budget lost -> no new entries in India or crypto today

Each lane's own kill switch (India ₹ limit / 3-loss streak, crypto $ limit)
still applies on top; this only adds the view across both.

Size suggestions are report-only: a net-negative strategy is flagged "cut to
smallest size" (1 lot in India), a strategy past the readiness bar and net-positive is flagged
"eligible for more — your call". Putting more real money on anything stays a
human decision; nothing here raises a size.
"""

from __future__ import annotations

import os
from typing import Any

TIGHTEN_AT = 0.5
SUGGEST_MIN_TRADES = 15
GROW_MIN_TRADES, GROW_MIN_DAYS = 30, 14


def _usd_inr() -> float:
    try:
        return float(os.getenv("CRYPTO_USDINR", "88") or 88)
    except ValueError:
        return 88.0


def _crypto_live_usd_today() -> float:
    try:
        from crypto.executor import _today_live_rows  # UTC day, same as crypto's own switch

        return sum(float(r.get("pnl_usd") or 0.0) for r in _today_live_rows())
    except Exception:
        return 0.0


def account_day() -> dict[str, Any]:
    from index_ai.learning import today_live_realized_pnl
    from index_ai.risk_policy import effective_risk_limits

    india = float(today_live_realized_pnl())
    crypto_usd = _crypto_live_usd_today()
    crypto = round(crypto_usd * _usd_inr(), 2)
    limit = abs(float(effective_risk_limits()["max_daily_loss_rupees"]))
    lost = max(0.0, -(india + crypto))
    used = lost / limit if limit else 0.0
    state = "STOPPED" if used >= 1 else "TIGHTENED" if used >= TIGHTEN_AT else "NORMAL"
    return {
        "state": state,
        "limit_rupees": limit,
        "lost_rupees": round(lost, 2),
        "used_pct": round(used * 100, 1),
        "india_live_rupees": round(india, 2),
        "crypto_live_usd": round(crypto_usd, 2),
        "crypto_live_rupees": crypto,
        "usd_inr": _usd_inr(),
        "message": {
            "NORMAL": "Normal size.",
            "TIGHTENED": f"Lost ₹{lost:,.0f} of ₹{limit:,.0f} today — new India trades are 1 lot.",
            "STOPPED": f"Lost ₹{lost:,.0f} of ₹{limit:,.0f} today across India + crypto — "
            "no new entries until tomorrow.",
        }[state],
    }


def india_lots(configured: int) -> int:
    """Lots for a new India trade: the dial, or 1 once the day is TIGHTENED."""
    try:
        return 1 if account_day()["state"] != "NORMAL" else configured
    except Exception:
        return configured  # a read failure must not change order size


def stopped() -> tuple[bool, str]:
    try:
        day = account_day()
    except Exception:
        return False, ""
    return day["state"] == "STOPPED", day["message"]


def size_suggestions() -> list[dict[str, Any]]:
    """Per (strategy, instrument), all modes combined, since the data epoch."""
    from collections import defaultdict

    from index_ai.strategy_performance import strategy_scorecard

    card = strategy_scorecard()
    merged: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "net": 0.0, "span_days": 0}
    )
    for venue in ("india", "crypto", "commodities"):
        for r in (card.get(venue) or {}).get("rows", []):
            m = merged[(venue, r["strategy"], r["instrument"])]
            m["trades"] += r["trades"]
            m["net"] += r["net"]
            m["currency"] = r["currency"]
            m["span_days"] = max(m["span_days"], _span(r))
    out = []
    for (venue, strategy, instrument), m in merged.items():
        n, net, days = m["trades"], round(m["net"], 2), m["span_days"]
        if n < SUGGEST_MIN_TRADES:
            action, why = (
                "collecting",
                f"{n} trades — too few to judge (needs {SUGGEST_MIN_TRADES})",
            )
        elif net < 0:
            action, why = "cut to smallest size", f"losing after charges over {n} trades"
        elif n >= GROW_MIN_TRADES and days >= GROW_MIN_DAYS:
            action, why = (
                "eligible for more — your call",
                f"making money over {n} trades, {days} days",
            )
        else:
            action, why = (
                "keep",
                f"making money over {n} trades, but under the {GROW_MIN_TRADES}-trade / {GROW_MIN_DAYS}-day bar",
            )
        out.append(
            {
                "venue": venue,
                "strategy": strategy,
                "instrument": instrument,
                "trades": n,
                "net": net,
                "currency": m.get("currency", "INR"),
                "suggestion": action,
                "why": why,
            }
        )
    order = {"cut to smallest size": 0, "eligible for more — your call": 1, "keep": 2, "collecting": 3}
    return sorted(out, key=lambda r: (order[r["suggestion"]], r["net"]))


def _span(row: dict[str, Any]) -> int:
    from datetime import date

    try:
        return (date.fromisoformat(row["last_day"]) - date.fromisoformat(row["first_day"])).days + 1
    except (KeyError, TypeError, ValueError):
        return 0
