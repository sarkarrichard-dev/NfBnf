from __future__ import annotations

from typing import Any

from index_ai.config import AppSettings
from index_ai.dhan import DhanClient
from index_ai.dhan_errors import classify_http_error
from index_ai.instruments import configured_index_keys, unconfigured_index_keys
from index_ai.planner import plan_instrument


def _cpr_position(price: float, bc: float, tc: float) -> str:
    if price > tc:
        return "above_cpr"
    if price < bc:
        return "below_cpr"
    return "inside_cpr"


def _ema_bias(ema_fast: float, ema_slow: float) -> str:
    if ema_fast > ema_slow:
        return "bullish"
    if ema_fast < ema_slow:
        return "bearish"
    return "flat"


def _heat_score(action: str, confidence: float) -> float:
    if action == "NO_TRADE":
        return 0.15
    return min(1.0, max(0.35, confidence))


def build_heatmap(client: DhanClient, app_settings: AppSettings) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for key in unconfigured_index_keys():
        cells.append(
            {
                "instrument": key,
                "error": f"{key} security id is not configured in .env.",
                "heat": 0.0,
                "action": "SKIP",
            }
        )
    for key in configured_index_keys():
        try:
            result = plan_instrument(client=client, app_settings=app_settings, instrument_key=key)
            if result.get("error"):
                cells.append(
                    {
                        "instrument": key,
                        "error": result["error"],
                        "heat": 0.0,
                        "action": "ERROR",
                    }
                )
                continue
            signal = result.get("signal") or {}
            regime = result.get("cpr_regime") or {}
            oi = result.get("oi") or {}
            plan = result.get("plan") or {}
            price = float(signal.get("price") or 0)
            bc = float(signal.get("bc") or 0)
            tc = float(signal.get("tc") or 0)
            action = str(signal.get("action") or "NO_TRADE")
            confidence = float(signal.get("confidence") or 0)
            cells.append(
                {
                    "instrument": key,
                    "action": action,
                    "confidence": confidence,
                    "price": price,
                    "pivot": signal.get("pivot"),
                    "bc": bc,
                    "tc": tc,
                    "cpr_position": _cpr_position(price, bc, tc),
                    "cpr_regime": regime.get("day_bias"),
                    "cpr_width_class": regime.get("width_class"),
                    "cpr_width_pct": regime.get("width_pct"),
                    "structure": signal.get("recommended_structure"),
                    "ema_bias": _ema_bias(
                        float(signal.get("ema_fast") or 0),
                        float(signal.get("ema_slow") or 0),
                    ),
                    "plan_allowed": bool(plan.get("allowed")),
                    "plan_reason": plan.get("reason"),
                    "heat": _heat_score(action, confidence),
                    "reason": signal.get("reason"),
                    "pcr": oi.get("pcr"),
                    "oi_bias": oi.get("bias"),
                    "call_oi": oi.get("total_call_oi"),
                    "put_oi": oi.get("total_put_oi"),
                }
            )
        except Exception as exc:
            friendly = str(classify_http_error(exc, f"{key} heatmap"))
            cells.append(
                {
                    "instrument": key,
                    "error": friendly,
                    "heat": 0.0,
                    "action": "ERROR",
                }
            )
    bullish = sum(1 for c in cells if c.get("action") == "BUY_CALL")
    bearish = sum(1 for c in cells if c.get("action") == "BUY_PUT")
    credit = sum(
        1
        for c in cells
        if str(c.get("action") or "").startswith("SELL_")
    )
    ready = sum(1 for c in cells if c.get("plan_allowed"))
    return {
        "cells": cells,
        "summary": {
            "bullish_signals": bullish,
            "bearish_signals": bearish,
            "credit_signals": credit,
            "executable": ready,
            "scanned": len(cells),
        },
    }
