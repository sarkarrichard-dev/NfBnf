"""Trailing take-profit on open-trade MTM (no fixed profit cap)."""

from __future__ import annotations

from typing import Any

from index_ai.strategy_params import StrategyParams, get_strategy_params


def profit_trail_settings(params: StrategyParams | None = None) -> dict[str, Any]:
    cfg = params or get_strategy_params()
    from index_ai.trade_lots import get_lots_per_trade

    lots = get_lots_per_trade()
    arm_base = float(cfg.profit_trail_arm_rupees_per_lot)
    return {
        "enable_profit_trail": cfg.enable_profit_trail,
        "profit_trail_arm_rupees": round(arm_base * lots, 2),
        "profit_trail_arm_rupees_per_lot": arm_base,
        "profit_trail_giveback_pct": float(cfg.profit_trail_giveback_pct),
        "lots_per_trade": lots,
    }


def attach_profit_trail_meta(meta: dict[str, Any], *, params: StrategyParams | None = None) -> dict[str, Any]:
    """Add profit-trail config to trail_meta (peak starts at entry)."""
    settings = profit_trail_settings(params)
    out = {**settings, **meta}
    out.setdefault("peak_mtm_pnl", None)
    out.setdefault("profit_trail_armed", False)
    out.setdefault("profit_trail_floor_rupees", None)
    out["use_profit_trail"] = bool(settings["enable_profit_trail"])
    return out


def update_profit_trail(meta: dict[str, Any], mtm_pnl: float) -> dict[str, Any]:
    """
    Track peak MTM profit; once armed, exit when profit gives back giveback_pct from peak.
    No upper profit cap — only trails higher.
    """
    if not meta.get("enable_profit_trail"):
        return {**meta, "profit_trail_hit": False}

    pnl = float(mtm_pnl)
    peak = meta.get("peak_mtm_pnl")
    peak_val = pnl if peak is None else max(float(peak), pnl)
    arm_at = float(meta.get("profit_trail_arm_rupees") or 0)
    giveback = float(meta.get("profit_trail_giveback_pct") or 0.25)
    armed = bool(meta.get("profit_trail_armed"))

    if not armed and peak_val >= arm_at:
        armed = True

    floor: float | None = None
    hit = False
    if armed and peak_val > 0 and giveback > 0:
        floor = round(peak_val * (1.0 - giveback), 2)
        if pnl <= floor:
            hit = True

    return {
        **meta,
        "peak_mtm_pnl": round(peak_val, 2),
        "last_mtm_pnl": round(pnl, 2),
        "profit_trail_armed": armed,
        "profit_trail_floor_rupees": floor,
        "profit_trail_hit": hit,
    }


def profit_trail_exit_reason(meta: dict[str, Any]) -> str | None:
    if not meta.get("profit_trail_hit"):
        return None
    peak = float(meta.get("peak_mtm_pnl") or 0)
    floor = float(meta.get("profit_trail_floor_rupees") or 0)
    pnl = float(meta.get("last_mtm_pnl") or 0)
    pct = int(float(meta.get("profit_trail_giveback_pct") or 0.25) * 100)
    return (
        f"Profit trail: ₹{pnl:,.0f} fell to floor ₹{floor:,.0f} "
        f"({pct}% giveback from peak ₹{peak:,.0f})."
    )


def evaluate_profit_trail(
    meta: dict[str, Any],
    mtm_pnl: float | None,
) -> tuple[dict[str, Any], bool, str | None]:
    if mtm_pnl is None:
        return meta, False, None
    updated = update_profit_trail(meta, float(mtm_pnl))
    if updated.get("profit_trail_hit"):
        return updated, True, profit_trail_exit_reason(updated)
    return updated, False, None
