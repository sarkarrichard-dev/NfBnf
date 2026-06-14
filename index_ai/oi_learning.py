"""Learn from closed trades tagged with option-chain OI (PCR, bias) and suggest tuning."""

from __future__ import annotations

from typing import Any

from index_ai.ml_outcomes import _is_excluded_trade_id


def _oi_bias_code(bias: str) -> float:
    b = (bias or "").strip().lower()
    if b == "call_heavy":
        return 1.0
    if b == "put_heavy":
        return -1.0
    if b == "neutral":
        return 0.0
    return 0.0


def _trade_oi_fields(trade: dict[str, Any]) -> dict[str, Any] | None:
    option = trade.get("option") or {}
    feats = option.get("ml_features") or {}
    pcr = option.get("chain_pcr") or option.get("pcr") or feats.get("pcr")
    bias = option.get("chain_bias") or option.get("oi_bias") or ""
    if pcr is None and not bias:
        return None
    try:
        pcr_f = float(pcr) if pcr is not None else None
    except (TypeError, ValueError):
        pcr_f = None
    return {
        "pcr": pcr_f,
        "bias": str(bias or "unknown"),
        "oi_conf_adj": float(
            option.get("oi_confidence_adjustment") or feats.get("oi_conf_adj") or 0.0
        ),
    }


def _pcr_bucket(pcr: float | None) -> str:
    if pcr is None:
        return "unknown"
    if pcr < 0.9:
        return "low_pcr"
    if pcr > 1.15:
        return "high_pcr"
    return "mid_pcr"


def analyze_oi_outcomes(*, min_samples: int = 3) -> dict[str, Any]:
    """Win rates by OI bias / PCR bucket from closed trades."""
    from index_ai.learning import connect, _row_to_trade

    bias_stats: dict[str, dict[str, float]] = {}
    pcr_stats: dict[str, dict[str, float]] = {}
    aligned = {"wins": 0, "losses": 0}
    counter = {"wins": 0, "losses": 0}
    total_with_oi = 0

    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM trades
            WHERE pnl IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 120
            """
        ).fetchall()

    for raw in rows:
        trade = _row_to_trade(raw)
        tid = str(trade.get("id") or "")
        if _is_excluded_trade_id(tid):
            continue
        oi = _trade_oi_fields(trade)
        if not oi:
            continue
        total_with_oi += 1
        pnl = float(trade.get("pnl") or 0)
        win = pnl > 0
        action = str(trade.get("action") or "")
        bias = oi["bias"]
        bucket = _pcr_bucket(oi.get("pcr"))

        for key, store in ((bias, bias_stats), (bucket, pcr_stats)):
            if key not in store:
                store[key] = {"trades": 0, "wins": 0, "pnl_sum": 0.0}
            store[key]["trades"] += 1
            store[key]["wins"] += 1 if win else 0
            store[key]["pnl_sum"] += pnl

        if action == "BUY_CALL":
            if bias == "call_heavy":
                aligned["wins" if win else "losses"] += 1
            elif bias == "put_heavy":
                counter["wins" if win else "losses"] += 1
        elif action == "BUY_PUT":
            if bias == "put_heavy":
                aligned["wins" if win else "losses"] += 1
            elif bias == "call_heavy":
                counter["wins" if win else "losses"] += 1

    def _finalize(store: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for label, s in sorted(store.items(), key=lambda x: -x[1]["trades"]):
            n = int(s["trades"])
            wins = int(s["wins"])
            out.append(
                {
                    "label": label,
                    "trades": n,
                    "win_rate": round(wins / n, 3) if n else None,
                    "avg_pnl": round(s["pnl_sum"] / n, 2) if n else None,
                    "sufficient_data": n >= min_samples,
                }
            )
        return out

    recommendations: list[str] = []
    a_total = aligned["wins"] + aligned["losses"]
    c_total = counter["wins"] + counter["losses"]
    if a_total >= min_samples:
        a_wr = aligned["wins"] / a_total
        if a_wr >= 0.55:
            recommendations.append(
                f"OI-aligned setups win {a_wr:.0%} ({a_total} trades) — keep current OI confidence boosts."
            )
        elif a_wr < 0.4:
            recommendations.append(
                f"OI-aligned setups only win {a_wr:.0%} — consider tightening REQUIRE_SUPERTREND_ALIGN or CPR gates."
            )
    if c_total >= min_samples:
        c_wr = counter["wins"] / c_total
        if c_wr < 0.35:
            recommendations.append(
                f"Trades against chain OI bias lose often ({c_wr:.0%} win rate, {c_total} trades) — "
                "OI filter is doing its job; avoid overriding it manually."
            )
        elif c_wr > 0.5:
            recommendations.append(
                f"Counter-OI trades still win {c_wr:.0%} — OI penalty may be too harsh; review oi_confidence_adjustment."
            )

    for row in _finalize(bias_stats):
        if not row["sufficient_data"]:
            continue
        wr = row["win_rate"] or 0
        if wr >= 0.6:
            recommendations.append(
                f"When chain bias is {row['label']}, recent win rate is {wr:.0%} ({row['trades']} trades)."
            )
        elif wr <= 0.3:
            recommendations.append(
                f"Avoid or tighten entries when chain bias is {row['label']} (win rate {wr:.0%})."
            )

    if total_with_oi < min_samples:
        message = (
            f"Need at least {min_samples} closed trades with OI tags (chain_pcr / chain_bias). "
            f"Found {total_with_oi}."
        )
    else:
        message = f"Analyzed {total_with_oi} closed trades with option-chain OI."

    return {
        "trades_with_oi": total_with_oi,
        "by_bias": _finalize(bias_stats),
        "by_pcr_bucket": _finalize(pcr_stats),
        "oi_aligned": {
            "trades": a_total,
            "win_rate": round(aligned["wins"] / a_total, 3) if a_total else None,
        },
        "oi_counter": {
            "trades": c_total,
            "win_rate": round(counter["wins"] / c_total, 3) if c_total else None,
        },
        "recommendations": recommendations,
        "message": message,
        "active": total_with_oi >= min_samples,
    }


def oi_bias_feature(bias: str) -> float:
    return _oi_bias_code(bias)
