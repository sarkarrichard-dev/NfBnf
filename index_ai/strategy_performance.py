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

from index_ai.data_epoch import data_epoch
from index_ai.market_clock import now_ist_iso

_MEANINGLESS_MODE = {"", "none", "wait", "conflict"}


def _after_epoch(when: str | None, epoch: str | None) -> bool:
    """Keep a row only if its timestamp is at/after the data epoch. Missing epoch
    keeps everything; missing row timestamp is kept (fail open). Both timestamps
    are ISO-8601 IST, so a lexical compare of the ``YYYY-MM-DDTHH:MM:SS`` prefix
    is an ordering compare."""
    if not epoch or not when:
        return True
    return str(when)[:19] >= epoch[:19]


def _india_strategy(trade: dict[str, Any]) -> str:
    """The strategy label for a row: its ``strategy_mode`` if it has a real one,
    else the buy/sell lane.

    The buy lane's ``candlestick_buy`` mode covers six different patterns
    (engulfing, hammer, breakout, trend-pullback, …) lumped into one bucket —
    2026-09-16, after a bad day traced to one specific pattern type, split it
    by the real pattern (``entry_quality`` on the signal, already persisted
    per trade) so each pattern's own win rate is visible, not averaged away.
    """
    signal = trade.get("signal") or {}
    mode = str(signal.get("strategy_mode") or "").strip()
    if mode and mode.lower() not in _MEANINGLESS_MODE:
        if mode == "candlestick_buy":
            pattern = str(signal.get("entry_quality") or "").strip()
            if pattern:
                return f"{mode} · {pattern}"
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


def _india_charge_breakdown(trade: dict[str, Any]) -> dict[str, float] | None:
    """Itemised brokerage/STT/exchange-txn/SEBI/GST/stamp for one India round
    trip — the same total as ``_india_charges``, split into the lines a real
    Dhan contract note shows."""
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
        from index_ai.charges import round_trip_charge_breakdown

        exchange = "BSE" if inst.upper() == "SENSEX" else "NSE"
        return round_trip_charge_breakdown(option, qty, exchange=exchange)
    except Exception:
        return None


_CHARGE_ITEMS = ("brokerage", "stt", "exch_txn", "sebi", "gst", "stamp")


def _blank_bucket() -> dict[str, Any]:
    return {
        "pnls": [],
        "gross": 0.0,
        "charges": 0.0,
        "slippage": 0.0,
        "priced": 0,
        "days": set(),
        "charge_items": dict.fromkeys(_CHARGE_ITEMS, 0.0),
    }


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
        # itemised: brokerage/STT/exchange txn/SEBI/GST/stamp paid, the lines
        # of a real contract note — India only for now (crypto/commodities
        # journals already net the charge at exit, with no line items kept).
        "charge_breakdown": {k: round(v, 2) for k, v in b["charge_items"].items()},
    }


def _india_rows() -> list[dict[str, Any]]:
    from index_ai.learning import recent_trades

    epoch = data_epoch()
    buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(_blank_bucket)
    for t in recent_trades(limit=1_000_000):
        if t.get("pnl") is None or not _after_epoch(t.get("created_at"), epoch):
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
            items = _india_charge_breakdown(t)
            if items:
                for k in _CHARGE_ITEMS:
                    b["charge_items"][k] += items.get(k, 0.0)
        else:
            b["pnls"].append(gross)  # unpriced — gross is the best we have
        day = str(t.get("created_at") or "")[:10]
        if day:
            b["days"].add(day)
    return [_finish(k, v, "INR") for k, v in buckets.items()]


def _crypto_rows(*, keep_days: bool = False) -> list[dict[str, Any]]:
    from crypto.journal import JOURNAL_PATH

    epoch = data_epoch()
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
        if not _after_epoch(r.get("closed_at") or r.get("day"), epoch):
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
    rows = []
    for k, v in buckets.items():
        row = _finish(k, v, "USD")
        if keep_days:
            row["_days"] = set(v["days"])
        rows.append(row)
    return rows


def _commodities_rows() -> list[dict[str, Any]]:
    """MCX commodity paper journal — `memory/commodity_journal.jsonl`. Each row
    already carries gross_rupees / friction_rupees / net_rupees with the MCX
    charge schedule applied at exit, so nothing to recompute (like crypto)."""
    from commodities.lanes import JOURNAL_PATH

    epoch = data_epoch()
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
        if not _after_epoch(r.get("exit_time") or r.get("day"), epoch):
            continue
        inst = str(r.get("instrument") or "?")
        mode = str(r.get("mode") or "PAPER").upper()
        b = buckets[("commodities", f"directional|{inst}", mode)]
        pnl = float(r.get("net_rupees") or 0.0)
        b["pnls"].append(pnl)
        b["gross"] += float(r.get("gross_rupees") or pnl)
        b["charges"] += float(r.get("friction_rupees") or 0.0)
        b["priced"] += 1
        day = str(r.get("day") or r.get("exit_time") or "")[:10]
        if day:
            b["days"].add(day)
    return [_finish(k, v, "INR") for k, v in buckets.items()]


def _totals(rows: list[dict[str, Any]], currency: str) -> dict[str, Any]:
    n = sum(r["trades"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    gross = round(sum(r["gross"] for r in rows), 2)
    charges = round(sum(r["charges"] for r in rows), 2)
    slippage = round(sum(r["slippage"] for r in rows), 2)
    charge_breakdown = {
        k: round(sum(r.get("charge_breakdown", {}).get(k, 0.0) for r in rows), 2)
        for k in _CHARGE_ITEMS
    }
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
        # itemised — India only for now; zero/empty for crypto/commodities
        "charge_breakdown": charge_breakdown,
    }


def strategy_scorecard() -> dict[str, Any]:
    india = sorted(_india_rows(), key=lambda r: (r["strategy"], r["instrument"], r["mode"]))
    crypto = sorted(_crypto_rows(), key=lambda r: (r["strategy"], r["instrument"], r["mode"]))
    commodities = sorted(
        _commodities_rows(), key=lambda r: (r["strategy"], r["instrument"], r["mode"])
    )
    return {
        "generated_at": now_ist_iso(),
        "india": {"rows": india, "totals": _totals(india, "INR")},
        "crypto": {"rows": crypto, "totals": _totals(crypto, "USD")},
        "commodities": {"rows": commodities, "totals": _totals(commodities, "INR")},
        "note": (
            "India pnl is gross premium; charges are the Dhan schedule "
            "(₹20/order + STT + exchange txn + SEBI + GST + stamp) plus measured "
            "slippage. Crypto fees are the real Delta taker fee + GST applied at exit. "
            "Commodities (MCX) already carry the CTT/txn/GST schedule net at exit."
        ),
    }


# The "go live with real money" bar for a crypto strategy, agreed with Richard
# 2026-09-17 after he asked about going live next month: a handful of good
# paper days isn't proof of an edge (the India sell lane looked fine under 30
# trades too, then got worse). Live is a data-driven crossing, not a calendar
# date — none of the five crypto strategies clear this yet.
CRYPTO_LIVE_MIN_TRADES = 30
CRYPTO_LIVE_MIN_DAYS = 14


def crypto_live_readiness() -> list[dict[str, Any]]:
    """Per-strategy (all its instruments combined) readiness verdict: enough
    trades, enough calendar days, and net positive over that whole window.
    Also reports how many of its instruments are individually net-positive,
    since an aggregate profit from one strong coin carrying several weak ones
    isn't the same as a real, broad edge."""
    by_strategy: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "net": 0.0, "days": set(), "instruments": {}}
    )
    for row in _crypto_rows(keep_days=True):
        if row["mode"] != "PAPER":
            continue
        s = by_strategy[row["strategy"]]
        s["trades"] += row["trades"]
        s["net"] += row["net"]
        # every day it actually traded -- this used to add only each coin's
        # first and last day, so "days" undercounted (e.g. 8 for ak_roxx_pro)
        s["days"] |= row["_days"]
        s["instruments"][row["instrument"]] = (row["net"], row["trades"])

    out = []
    for strat, s in sorted(by_strategy.items()):
        span_days = len(s["days"])
        insts = {k: net for k, (net, _) in s["instruments"].items()}
        ready = (
            s["trades"] >= CRYPTO_LIVE_MIN_TRADES
            and span_days >= CRYPTO_LIVE_MIN_DAYS
            and s["net"] > 0
        )
        out.append(
            {
                "strategy": strat,
                "trades": s["trades"],
                "trades_needed": CRYPTO_LIVE_MIN_TRADES,
                "days_span": span_days,
                "days_needed": CRYPTO_LIVE_MIN_DAYS,
                "net_usd": round(s["net"], 2),
                "instruments_positive": sum(1 for v in insts.values() if v > 0),
                "instruments_total": len(insts),
                "ready": ready,
                "why_not": None
                if ready
                else (
                    f"needs {CRYPTO_LIVE_MIN_TRADES}+ trades ({s['trades']} so far)"
                    if s["trades"] < CRYPTO_LIVE_MIN_TRADES
                    else f"needs {CRYPTO_LIVE_MIN_DAYS}+ days of data ({span_days} so far)"
                    if span_days < CRYPTO_LIVE_MIN_DAYS
                    else f"net negative over the window (${s['net']:.2f})"
                ),
            }
        )
    return out


CRYPTO_PAIR_MIN_TRADES = 5


def crypto_live_pairs() -> set[tuple[str, str]]:
    """(strategy, coin) pairs allowed to trade real money once crypto is armed.

    The strategy must pass the readiness bar above AND that coin must be
    net-positive for it over at least CRYPTO_PAIR_MIN_TRADES paper trades.
    Every other pair keeps paper-trading alongside -- arming used to send
    every enabled strategy on every coin live at once, losers included.
    """
    ready = {r["strategy"] for r in crypto_live_readiness() if r["ready"]}
    return {
        (row["strategy"], row["instrument"])
        for row in _crypto_rows()
        if row["mode"] == "PAPER"
        and row["strategy"] in ready
        and row["trades"] >= CRYPTO_PAIR_MIN_TRADES
        and row["net"] > 0
    }


def crypto_live_pair_table() -> list[dict[str, Any]]:
    """Every paper (strategy, coin) with whether it would go live, and why not."""
    ready = {r["strategy"]: r for r in crypto_live_readiness()}
    allowed = crypto_live_pairs()
    out = []
    for row in sorted(_crypto_rows(), key=lambda r: (r["strategy"], -r["net"])):
        if row["mode"] != "PAPER":
            continue
        pair = (row["strategy"], row["instrument"])
        strat = ready.get(row["strategy"]) or {}
        why = (None if pair in allowed
               else f"strategy not ready: {strat.get('why_not')}" if not strat.get("ready")
               else f"only {row['trades']} trades on this coin (needs {CRYPTO_PAIR_MIN_TRADES})"
               if row["trades"] < CRYPTO_PAIR_MIN_TRADES
               else f"losing on this coin (${row['net']:.2f})")
        out.append({"strategy": row["strategy"], "coin": row["instrument"],
                    "trades": row["trades"], "net_usd": row["net"],
                    "goes_live": pair in allowed, "why_not": why})
    return out


if __name__ == "__main__":  # self-check — runs against the real journals, no network
    sc = strategy_scorecard()
    assert set(sc) == {"generated_at", "india", "crypto", "commodities", "note"}
    for side in ("india", "crypto", "commodities"):
        assert set(sc[side]) == {"rows", "totals"}
        for row in sc[side]["rows"]:
            assert row["wins"] + row["losses"] <= row["trades"]  # rest are scratches
            assert abs(row["net"] - (row["gross"] - row["charges"] - row["slippage"])) < 0.02
    print(
        "strategy_performance self-check ok — "
        + ", ".join(
            f"{s} {sc[s]['totals']['trades']} trades / {len(sc[s]['rows'])} rows"
            for s in ("india", "crypto", "commodities")
        )
    )

    readiness = crypto_live_readiness()
    for r in readiness:
        assert r["trades"] >= 0 and r["days_span"] >= 0
        assert r["ready"] or r["why_not"]  # every non-ready verdict must say why
    print(
        "crypto_live_readiness ok — "
        + ", ".join(f"{r['strategy']}={'READY' if r['ready'] else 'not yet'}" for r in readiness)
    )
