"""Per-(strategy, instrument) scorecard from the live journals.

The point: see how **each strategy** is doing **on each instrument** — because
the same logic can win on one and lose on another (BTC vs ETH vs PAXG; NIFTY vs
BANKNIFTY, where a 10-point NIFTY move is a 60-100-point BANKNIFTY move and the
option pricing behaves differently).

Two sources, kept separate because the currency and the cost model differ:

* **India** — ``memory/trade_memory.sqlite``. ``pnl`` on a row is **gross**
  (premium in/out, no charges), so the charge model in ``index_ai.charges`` is
  applied here to get net: Dhan flat ₹20/order + STT + exchange txn + SEBI + GST
  + stamp, plus the measured bid-ask slippage.
* **Crypto** — ``memory/crypto_journal.jsonl``. Each row already carries
  ``gross_usd`` / ``fees_usd`` / ``pnl_usd`` with the real Delta fee applied at
  exit, so nothing to recompute.

Read-only. No trade behaviour depends on this.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from index_ai.market_clock import now_ist_iso

_MEANINGLESS_MODE = {"", "none", "wait", "conflict"}


def _india_strategy(trade: dict[str, Any]) -> str:
    """The strategy label for a row: its ``strategy_mode`` if it has a real one,
    else the buy/sell lane."""
    mode = str((trade.get("signal") or {}).get("strategy_mode") or "").strip()
    if mode and mode.lower() not in _MEANINGLESS_MODE:
        return mode
    from index_ai.strategies.strategy_router import trade_lane

    return {"buy": "buy (uncategorised)", "sell": "sell (uncategorised)"}.get(
        trade_lane(str(trade.get("action") or "")), "other"
    )


def _india_charges(trade: dict[str, Any]) -> tuple[float, float] | None:
    """(broker+statutory charges, slippage) in ₹ for one India round trip, or
    None when the row has no premium to price it from."""
    option = trade.get("option") or {}
    inst = str(trade.get("instrument") or option.get("instrument") or "NIFTY")
    qty = int(option.get("quantity") or 0)
    legs = option.get("legs") or []
    priced = (
        any(float(leg.get("ltp") or 0) > 0 for leg in legs) or float(option.get("ltp") or 0) > 0
    )
    if qty <= 0 or not priced:
        return None
    try:
        from index_ai.charges import estimate_trade_cost

        c = estimate_trade_cost(option, qty, inst)
        return float(c.charges_rupees), float(c.slippage_rupees)
    except Exception:
        return None


def _blank_bucket() -> dict[str, Any]:
    return {"pnls": [], "gross": 0.0, "charges": 0.0, "slippage": 0.0, "priced": 0, "days": set()}


def _finish(key: tuple[str, str, str], b: dict[str, Any], currency: str) -> dict[str, Any]:
    venue, strategy, instrument, mode = key[0], key[1].split("|")[0], key[1].split("|")[1], key[2]
    pnls = b["pnls"]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = b["gross"] - b["charges"] - b["slippage"]
    days = sorted(b["days"])
    return {
        "venue": venue,
        "strategy": strategy,
        "instrument": instrument,
        "mode": mode,
        "currency": currency,
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 3) if n else None,
        "gross": round(b["gross"], 2),
        "charges": round(b["charges"], 2),
        "slippage": round(b["slippage"], 2),
        "net": round(net, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy": round(net / n, 2) if n else 0.0,
        "best": round(max(pnls), 2) if pnls else 0.0,
        "worst": round(min(pnls), 2) if pnls else 0.0,
        "priced_pct": round(b["priced"] / n, 2) if n else None,  # share of rows we could cost
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
    }


def _india_rows() -> list[dict[str, Any]]:
    from index_ai.learning import recent_trades

    buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(_blank_bucket)
    for t in recent_trades(limit=1_000_000):
        if t.get("pnl") is None:
            continue
        strat = _india_strategy(t)
        inst = str(t.get("instrument") or "?")
        mode = str(t.get("mode") or "PAPER").upper()
        b = buckets[("india", f"{strat}|{inst}", mode)]
        gross = float(t["pnl"])  # India journal pnl is gross
        b["gross"] += gross
        cs = _india_charges(t)
        if cs is not None:
            b["charges"] += cs[0]
            b["slippage"] += cs[1]
            b["priced"] += 1
            b["pnls"].append(gross - cs[0] - cs[1])  # net per trade, when we can cost it
        else:
            b["pnls"].append(gross)  # unpriced — gross is the best we have
        day = str(t.get("created_at") or "")[:10]
        if day:
            b["days"].add(day)
    return [_finish(k, v, "INR") for k, v in buckets.items()]


def _crypto_rows() -> list[dict[str, Any]]:
    from crypto.journal import JOURNAL_PATH

    buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(_blank_bucket)
    if not JOURNAL_PATH.is_file():
        return []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        strat = str(r.get("strategy") or "?")
        asset = str(r.get("asset") or "?")
        mode = str(r.get("mode") or "paper").upper()
        b = buckets[("crypto", f"{strat}|{asset}", mode)]
        pnl = float(r.get("pnl_usd") or 0.0)
        b["pnls"].append(pnl)
        b["gross"] += float(r.get("gross_usd") or pnl)
        b["charges"] += float(r.get("fees_usd") or 0.0)
        b["priced"] += 1
        day = str(r.get("day") or r.get("closed_at") or "")[:10]
        if day:
            b["days"].add(day)
    return [_finish(k, v, "USD") for k, v in buckets.items()]


def _totals(rows: list[dict[str, Any]], currency: str) -> dict[str, Any]:
    n = sum(r["trades"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    gross = round(sum(r["gross"] for r in rows), 2)
    charges = round(sum(r["charges"] for r in rows), 2)
    slippage = round(sum(r["slippage"] for r in rows), 2)
    return {
        "currency": currency,
        "strategies": len({r["strategy"] for r in rows}),
        "instruments": len({r["instrument"] for r in rows}),
        "trades": n,
        "win_rate": round(wins / n, 3) if n else None,
        "gross": gross,
        "charges": charges,
        "slippage": slippage,
        "net": round(gross - charges - slippage, 2),
    }


def strategy_scorecard() -> dict[str, Any]:
    india = sorted(_india_rows(), key=lambda r: (r["strategy"], r["instrument"], r["mode"]))
    crypto = sorted(_crypto_rows(), key=lambda r: (r["strategy"], r["instrument"], r["mode"]))
    return {
        "generated_at": now_ist_iso(),
        "india": {"rows": india, "totals": _totals(india, "INR")},
        "crypto": {"rows": crypto, "totals": _totals(crypto, "USD")},
        "note": (
            "India pnl is gross premium; charges are the Dhan schedule "
            "(₹20/order + STT + exchange txn + SEBI + GST + stamp) plus measured "
            "slippage. Crypto fees are the real Delta taker fee + GST applied at exit."
        ),
    }


if __name__ == "__main__":  # self-check — runs against the real journals, no network
    sc = strategy_scorecard()
    assert set(sc) == {"generated_at", "india", "crypto", "note"}
    for side in ("india", "crypto"):
        assert set(sc[side]) == {"rows", "totals"}
        for row in sc[side]["rows"]:
            assert row["wins"] + row["losses"] <= row["trades"]  # rest are scratches
            assert abs(row["net"] - (row["gross"] - row["charges"] - row["slippage"])) < 0.02
    ni = sc["india"]["totals"]["trades"]
    nc = sc["crypto"]["totals"]["trades"]
    print(
        f"strategy_performance self-check ok — india {ni} trades / "
        f"{len(sc['india']['rows'])} rows, crypto {nc} trades / {len(sc['crypto']['rows'])} rows"
    )
