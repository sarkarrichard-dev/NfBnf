"""9:15–9:30 IST pre-open: OI, spot volume, CPR, EMA before first entry at 9:30."""

from __future__ import annotations

from typing import Any

from index_ai.config import AppSettings
from index_ai.dhan import DhanClient
from index_ai.instruments import configured_index_keys
from index_ai.market_clock import format_ist_display, now_ist_iso, today_ist_date
from index_ai.planner import plan_instrument

_ACTIVE_BRIEF: dict[str, Any] | None = None
_ACTIVE_BRIEF_DATE: str | None = None


def get_active_brief() -> dict[str, Any] | None:
    if _ACTIVE_BRIEF_DATE == today_ist_date():
        return _ACTIVE_BRIEF
    return None


def set_active_brief(brief: dict[str, Any]) -> None:
    global _ACTIVE_BRIEF, _ACTIVE_BRIEF_DATE
    _ACTIVE_BRIEF = brief
    _ACTIVE_BRIEF_DATE = today_ist_date()


def entry_confidence_bump(brief: dict[str, Any] | None = None) -> float:
    data = brief or get_active_brief()
    if not data:
        return 0.0
    return float(data.get("confidence_bump") or 0.0)


def brief_summary(brief: dict[str, Any] | None = None) -> str | None:
    data = brief or get_active_brief()
    if not data:
        return None
    return str(data.get("summary") or "") or None


def _index_analysis_line(key: str, snap: dict[str, Any]) -> str:
    spot = snap.get("spot_session") or {}
    oi = snap.get("oi") or {}
    parts = [
        key,
        str(snap.get("action") or "NO_TRADE"),
        f"CPR {spot.get('cpr_day_bias') or snap.get('cpr_regime') or '—'}",
        f"{spot.get('cpr_position') or '—'}",
        f"EMA {spot.get('ema_bias') or '—'}",
    ]
    if oi.get("pcr") is not None:
        parts.append(f"PCR {float(oi['pcr']):.2f}")
    if oi.get("bias"):
        parts.append(str(oi["bias"]))
    if spot.get("session_volume"):
        parts.append(f"vol {int(spot['session_volume']):,}")
    conf = snap.get("confidence")
    if conf is not None:
        parts.append(f"{float(conf):.0%}")
    return " · ".join(parts)


def _derive_notes_from_analysis(snapshots: dict[str, Any], notes: list[str]) -> None:
    """Extra gates from pre-open OI/volume/CPR/EMA disagreement."""
    for key, snap in snapshots.items():
        if snap.get("error"):
            continue
        spot = snap.get("spot_session") or {}
        oi = snap.get("oi") or {}
        action = str(snap.get("action") or "")
        ema_bias = str(spot.get("ema_bias") or "")
        oi_bias = str(oi.get("bias") or "")
        if action == "BUY_CALL" and ema_bias == "bearish":
            notes.append(f"{key}: EMA bearish vs long call — cautious.")
        if action == "BUY_PUT" and ema_bias == "bullish":
            notes.append(f"{key}: EMA bullish vs long put — cautious.")
        if action == "BUY_CALL" and oi_bias == "put_heavy":
            notes.append(f"{key}: Put-heavy OI vs long call — lower conviction.")
        if action == "BUY_PUT" and oi_bias == "call_heavy":
            notes.append(f"{key}: Call-heavy OI vs long put — lower conviction.")
        if action.startswith("SELL_BEAR") and ema_bias == "bullish":
            notes.append(f"{key}: EMA bullish vs bear credit — cautious.")
        if action.startswith("SELL_BULL") and ema_bias == "bearish":
            notes.append(f"{key}: EMA bearish vs bull credit — cautious.")
        bars = int(spot.get("bars") or 0)
        if bars < 3:
            notes.append(f"{key}: Only {bars} spot bars — thin session data.")


async def build_pre_open_brief(client: DhanClient, cfg: AppSettings) -> dict[str, Any]:
    """
    Pre-open window (9:15–9:30 IST): refresh OI, spot volume, CPR, and EMA for both indices.
    Re-run each scanner cycle in that window so data stays current before 9:30 entry.
    """
    from index_ai.learning import learning_report, today_live_realized_pnl, trades_summary
    from index_ai.oi_learning import analyze_oi_outcomes

    learned = learning_report().get("learned") or {}
    historical_oi = analyze_oi_outcomes()
    summary_stats = trades_summary()
    snapshots: dict[str, Any] = {}
    notes: list[str] = []

    for key in configured_index_keys():
        try:
            result = plan_instrument(client=client, app_settings=cfg, instrument_key=key)
        except Exception as exc:
            snapshots[key] = {"error": str(exc)[:200]}
            continue
        signal = result.get("signal") or {}
        oi_ctx = result.get("oi") or {}
        plan = result.get("plan") or {}
        regime = result.get("cpr_regime") or {}
        spot = result.get("spot_session") or {}
        snapshots[key] = {
            "action": signal.get("action"),
            "confidence": signal.get("confidence"),
            "reason": (signal.get("reason") or "")[:240],
            "cpr_regime": regime.get("day_bias"),
            "cpr_width_class": regime.get("width_class"),
            "spot_session": spot,
            "oi": oi_ctx,
            "oi_fetch_error": result.get("oi_fetch_error"),
            "plan_allowed": plan.get("allowed"),
            "plan_reason": plan.get("reason"),
            "expiry": result.get("expiry"),
        }

    _derive_notes_from_analysis(snapshots, notes)

    confidence_bump = 0.0
    win_rate = learned.get("trade_win_rate")
    if win_rate is not None and float(win_rate) < 0.45:
        confidence_bump += 0.03
        notes.append(f"Recent win rate {float(win_rate):.0%} — +3% confidence today.")

    oi_recs = list(historical_oi.get("recommendations") or [])
    if oi_recs:
        notes.append(oi_recs[0][:180])

    ml = learned.get("ml") or {}
    if ml.get("ready") and ml.get("holdout_accuracy") is not None:
        acc = float(ml["holdout_accuracy"])
        if acc < 0.5:
            confidence_bump += 0.02
            notes.append(f"ML holdout {acc:.0%} — +2% confidence gate.")

    if len(notes) > 6:
        confidence_bump += 0.01

    live_pnl = today_live_realized_pnl()
    if live_pnl < 0:
        notes.append(f"Live realized PnL today ₹{live_pnl:,.0f} — selective entries.")

    index_lines = [_index_analysis_line(k, s) for k, s in snapshots.items() if not s.get("error")]

    summary_parts = [
        "Pre-open analysis (OI · spot volume · CPR · EMA).",
        "First entry at 9:30 AM IST.",
    ]
    if index_lines:
        summary_parts.append(" | ".join(index_lines))
    if notes:
        summary_parts.append(notes[0])

    brief = {
        "built_at": now_ist_iso(),
        "built_at_ist": format_ist_display(now_ist_iso()),
        "analysis_window": "9:15–9:30 IST",
        "entries_from": "9:30 IST",
        "learning": learned,
        "oi_insights": historical_oi,
        "trades_summary": summary_stats,
        "index_snapshots": snapshots,
        "confidence_bump": round(confidence_bump, 4),
        "notes": notes,
        "summary": " ".join(summary_parts),
    }
    set_active_brief(brief)
    return brief
