"""
One feature vector for every lane.

Each lane journals its trades in a different shape (options-sell in SQLite with a
``signal``/``option`` pair, futures and options-CPR as JSONL with a flat
``features`` dict). ``unified_features`` maps any of them onto the same named
vector so a single model can learn from all of them.

Lane identity is itself a feature (``is_buy_lane`` / ``is_credit`` / ``is_futures``)
so the model can learn "this setup works for selling but not buying" rather than
being trained separately per lane on too little data.
"""

from __future__ import annotations

from typing import Any

FEATURES: tuple[str, ...] = (
    # --- lane / structure identity ---
    "is_buy_lane",  # long premium (CE/PE buy)
    "is_credit",  # short premium (any sell structure)
    "is_futures",  # directional futures
    "is_hedged",  # has a defined-risk wing
    "is_bullish",
    # --- instrument ---
    "is_banknifty",
    "is_sensex",
    # --- signal state at entry ---
    "confidence",
    "cpr_width_pct",
    "dist_tc_pct",
    "dist_bc_pct",
    "ema_spread_pct",
    "atr_pct",
    "prev_day_range_pct",
    "ret_15m_pct",
    "volume_ratio",
    # --- chop / trend-agreement signals (Part F, capture-only for now) ---
    "open_range_pct",  # first-45-min high-low as % of open
    "trend_vs_cpr_agree",  # +1 intraday trend agrees with CPR bias, -1 conflict, 0 flat
    "dist_pivot_target_pct",  # distance to the next favourable pivot at entry, %
    # --- timing ---
    "minute_of_day",
    "weekday",
    # --- trade economics known at entry ---
    "risk_reward",  # target distance / stop distance
    "friction_pct_of_edge",  # round-trip cost / expected gross edge, when known
)

_BULLISH = {"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT", "LONG"}
_CREDIT_PREFIX = "SELL_"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        out = float(v)
        return out if out == out else default  # NaN guard
    except (TypeError, ValueError):
        return default


def _minute_of_day(raw: Any) -> float:
    s = str(raw or "")
    if "T" in s and len(s) >= 16:
        try:
            return float(s[11:13]) * 60 + float(s[14:16])
        except ValueError:
            return 720.0
    if " " in s and len(s) >= 16:
        try:
            return float(s[11:13]) * 60 + float(s[14:16])
        except ValueError:
            return 720.0
    return 720.0


def _weekday(raw: Any) -> float:
    from datetime import date

    s = str(raw or "")[:10]
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return float(date(y, m, d).weekday())
    except (ValueError, TypeError):
        return 2.0


def unified_features(trade: dict[str, Any]) -> dict[str, float] | None:
    """Map any lane's closed-trade row onto the shared vector. None if unusable."""
    lane = str(trade.get("lane") or "").lower()
    action = str(
        trade.get("action")
        or trade.get("structure")
        or trade.get("side")
        or trade.get("direction")
        or ""
    ).upper()
    inst = str(trade.get("instrument") or "NIFTY").upper()

    # lane-native feature dicts (futures / options_cpr journal rows carry these)
    nested = dict(trade.get("features") or {})
    signal = dict(trade.get("signal") or {})
    option = dict(trade.get("option") or {})
    legacy = dict(option.get("ml_features") or {})

    is_futures = lane == "futures" or action in {"LONG", "SHORT"}
    is_credit = action.startswith(_CREDIT_PREFIX)
    is_buy = (not is_credit and not is_futures) and action in {"CE", "PE", "BUY_CALL", "BUY_PUT"}
    if not (is_futures or is_credit or is_buy):
        return None

    price = _f(signal.get("price") or trade.get("entry_spot") or trade.get("entry"))
    tc = _f(signal.get("tc"), price)
    bc = _f(signal.get("bc"), price)
    entry_ts = trade.get("entry_time") or option.get("created_at") or trade.get("session")

    # risk:reward and cost ratio, when the row carries enough to compute them
    rr = 0.0
    if trade.get("target_premium") and trade.get("entry_premium") and trade.get("sl_premium"):
        stop = _f(trade["entry_premium"]) - _f(trade["sl_premium"])
        rr = (_f(trade["target_premium"]) - _f(trade["entry_premium"])) / stop if stop > 0 else 0.0
    gross = abs(_f(trade.get("gross_rupees")))
    fric = _f(trade.get("friction_rupees"))
    cost_ratio = fric / gross if gross > 0 else 0.0

    # Part F chop signals — read a precomputed value if the row has one, else
    # derive from what the entry snapshot carries; neutral (0.0) when absent.
    or_pct = _f(
        nested.get("open_range_pct") or signal.get("open_range_pct") or legacy.get("open_range_pct")
    )
    if "trend_vs_cpr_agree" in nested:
        tvc = _f(nested["trend_vs_cpr_agree"])
    else:
        it = str(signal.get("intraday_trend") or nested.get("intraday_trend") or "").upper()
        bias = str(signal.get("cpr_regime") or signal.get("cpr_bias") or "").upper()
        up, down = it == "UP", it == "DOWN"
        bull, bear = "BULL" in bias, "BEAR" in bias
        tvc = (
            1.0
            if (up and bull) or (down and bear)
            else -1.0
            if (up and bear) or (down and bull)
            else 0.0
        )
    pt = _f(signal.get("pivot_target") or option.get("pivot_target"))
    dist_pt = (
        abs(price - pt) / max(abs(price), 1.0) * 100.0
        if (price and pt)
        else _f(nested.get("dist_pivot_target_pct"))
    )

    out = {
        "is_buy_lane": 1.0 if is_buy else 0.0,
        "is_credit": 1.0 if is_credit else 0.0,
        "is_futures": 1.0 if is_futures else 0.0,
        "is_hedged": 1.0 if trade.get("long_strike") is not None else 0.0,
        "is_bullish": 1.0 if (action in _BULLISH or action == "CE") else 0.0,
        "is_banknifty": 1.0 if inst == "BANKNIFTY" else 0.0,
        "is_sensex": 1.0 if inst == "SENSEX" else 0.0,
        "confidence": _f(signal.get("confidence") or legacy.get("confidence")),
        "cpr_width_pct": _f(
            nested.get("cpr_width_pct")
            or signal.get("cpr_width_pct")
            or legacy.get("cpr_width_pct")
        ),
        "dist_tc_pct": _f(
            nested.get("dist_tc_pct")
            if "dist_tc_pct" in nested
            else (
                legacy.get("dist_tc_pct")
                if "dist_tc_pct" in legacy
                else ((price - tc) / max(abs(price), 1.0) * 100.0 if price else 0.0)
            )
        ),
        "dist_bc_pct": _f(
            nested.get("dist_bc_pct")
            if "dist_bc_pct" in nested
            else (
                legacy.get("dist_bc_pct")
                if "dist_bc_pct" in legacy
                else ((price - bc) / max(abs(price), 1.0) * 100.0 if price else 0.0)
            )
        ),
        "ema_spread_pct": _f(nested.get("ema_spread_pct") or legacy.get("ema_spread_pct")),
        "atr_pct": _f(nested.get("atr_pct")),
        "prev_day_range_pct": _f(nested.get("prev_day_range_pct")),
        "ret_15m_pct": _f(nested.get("ret_15m_pct")),
        "volume_ratio": _f(
            nested.get("vol_ratio") or signal.get("volume_ratio") or legacy.get("volume_ratio"), 1.0
        ),
        "open_range_pct": or_pct,
        "trend_vs_cpr_agree": tvc,
        "dist_pivot_target_pct": dist_pt,
        "minute_of_day": _f(nested.get("minute_of_day")) or _minute_of_day(entry_ts),
        "weekday": _f(nested.get("weekday")) if "weekday" in nested else _weekday(entry_ts),
        "risk_reward": rr,
        "friction_pct_of_edge": min(5.0, cost_ratio),
    }
    return {k: _f(out.get(k)) for k in FEATURES}


def label(trade: dict[str, Any]) -> int | None:
    """1 = net-profitable close, 0 = not. None if the trade is still open."""
    for k in ("net_rupees", "pnl", "net_pnl"):
        if trade.get(k) is not None:
            return 1 if _f(trade[k]) > 0 else 0
    return None


if __name__ == "__main__":  # ponytail self-check
    fut = {
        "lane": "futures",
        "instrument": "NIFTY",
        "direction": "LONG",
        "entry_time": "2026-08-29T10:35:00+05:30",
        "net_rupees": 500.0,
    }
    cpr = {
        "lane": "sell",
        "instrument": "SENSEX",
        "structure": "SELL_BULL_PUT_SPREAD",
        "long_strike": 79000.0,
        "entry_time": "2026-08-29T11:05:00+05:30",
        "features": {"cpr_width_pct": 0.4, "atr_pct": 0.2, "minute_of_day": 665.0},
        "gross_rupees": 900.0,
        "friction_rupees": 220.0,
        "net_rupees": 680.0,
    }
    buy = {
        "instrument": "NIFTY",
        "side": "CE",
        "entry_time": "2026-08-29T09:50:00+05:30",
        "entry_premium": 120.0,
        "sl_premium": 96.0,
        "target_premium": 168.0,
        "net_rupees": -300.0,
    }
    for t, expect in ((fut, "is_futures"), (cpr, "is_credit"), (buy, "is_buy_lane")):
        v = unified_features(t)
        assert v is not None and v[expect] == 1.0, (expect, v)
        assert set(v) == set(FEATURES)
    assert unified_features(cpr)["is_hedged"] == 1.0
    assert abs(unified_features(cpr)["friction_pct_of_edge"] - 220 / 900) < 1e-6
    assert unified_features(buy)["risk_reward"] == 2.0
    assert unified_features(buy)["minute_of_day"] == 9 * 60 + 50

    agree = {
        "lane": "sell",
        "instrument": "NIFTY",
        "structure": "SELL_BEAR_CALL_SPREAD",
        "entry_time": "2026-08-29T11:05:00+05:30",
        "net_rupees": 100.0,
        "signal": {
            "intraday_trend": "DOWN",
            "cpr_regime": "TRENDING_BEAR",
            "price": 24000.0,
            "pivot_target": 23880.0,
            "open_range_pct": 0.42,
        },
    }
    v = unified_features(agree)
    assert v["trend_vs_cpr_agree"] == 1.0
    assert abs(v["open_range_pct"] - 0.42) < 1e-9
    assert abs(v["dist_pivot_target_pct"] - 0.5) < 1e-6
    conflict = {**agree, "signal": {**agree["signal"], "cpr_regime": "TRENDING_BULL"}}
    assert unified_features(conflict)["trend_vs_cpr_agree"] == -1.0
    assert label(fut) == 1 and label(buy) == 0 and label({}) is None
    assert unified_features({"action": "NO_TRADE"}) is None
    print("features.py self-check ok")
