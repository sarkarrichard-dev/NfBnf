"""When open positions should be closed by the scanner (time, regime, signal flip)."""

from __future__ import annotations

from typing import Any

from index_ai.market_clock import (
    is_square_off_window,
    now_ist,
    parse_ist_datetime,
    today_ist_date,
)
from index_ai.strategies.strategy_params import get_strategy_params

_BULLISH_ACTIONS = frozenset({"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"})
_BEARISH_ACTIONS = frozenset({"BUY_PUT", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_CALL"})


def _entry_strategy_mode(trade: dict[str, Any], signal: dict[str, Any]) -> str:
    return str(
        signal.get("strategy_mode") or (trade.get("signal") or {}).get("strategy_mode") or ""
    )


# Only the two-leg directional verticals — premium_trail watches a single short
# leg, so a two-sided structure (iron condor) still needs its regime close.
_TRAILED_VERTICALS = frozenset(
    {"SELL_BEAR_CALL_SPREAD", "SELL_BULL_PUT_SPREAD", "SELL_ATM_CALL", "SELL_ATM_PUT"}
)


def _premium_trailed_credit(trade: dict[str, Any]) -> bool:
    from index_ai.premium_trail import premium_trail_enabled

    action = str(trade.get("action") or (trade.get("signal") or {}).get("action") or "").upper()
    inst = str(trade.get("instrument") or (trade.get("option") or {}).get("instrument") or "")
    return action in _TRAILED_VERTICALS and premium_trail_enabled(inst)


def trade_created_ist_date(trade: dict[str, Any]) -> str | None:
    created = parse_ist_datetime(str(trade.get("created_at") or ""))
    if created is None:
        return None
    return created.date().isoformat()


def is_intraday_stale_open(trade: dict[str, Any]) -> bool:
    """Open row from a prior IST session (missed EOD square-off)."""
    if trade.get("pnl") is not None:
        return False
    entry_day = trade_created_ist_date(trade)
    if not entry_day:
        return False
    return entry_day < today_ist_date()


def is_mandatory_square_off_due(trade: dict[str, Any]) -> bool:
    """Same-day position still open in the exchange square-off window."""
    if trade.get("pnl") is not None:
        return False
    entry_day = trade_created_ist_date(trade)
    if entry_day != today_ist_date():
        return False
    return is_square_off_window(now_ist())


def strategy_exit_reason(
    trade: dict[str, Any],
    new_action: str,
    regime: dict[str, Any] | None,
    *,
    signal: dict[str, Any] | None = None,
) -> str | None:
    """Close when CPR regime, EMA flip, or a new signal opposes the open structure.

    Skipped for a credit spread on a premium-trailed index (NIFTY / BANKNIFTY):
    the entry signal was the thesis, and ``premium_trail`` owns the exit from
    there (a quarter-premium target, then a trailing stop, with a hard stop
    behind it). Bailing on a flip-floppy CPR read instead is what turned a flat
    BANKNIFTY day into 7 round-trips and -3,954 on 2026-09-02. Stale-open and the
    EOD square-off are handled elsewhere and still fire.
    """
    pos_action = str(trade.get("action") or (trade.get("signal") or {}).get("action") or "").upper()
    if not pos_action or pos_action == "NO_TRADE":
        return None

    if _premium_trailed_credit(trade):
        return None

    fresh = str(new_action or "NO_TRADE").upper()
    bias = str((regime or {}).get("day_bias") or "").upper()
    params = get_strategy_params()
    sig = signal or {}
    entry_mode = _entry_strategy_mode(trade, sig)
    intelligent = params.auto_intelligent_routing

    if params.exit_credit_on_ema_cross_flip:
        cross = str(sig.get("ema_cross") or "").upper()
        aligned = str(sig.get("ema_aligned") or "").lower()
        fast_p, slow_p = params.ema_fast_period, params.ema_slow_period
        if pos_action in _BEARISH_ACTIONS and (cross == "UP" or aligned == "bull"):
            detail = (
                f"EMA {fast_p}/{slow_p} bullish cross"
                if cross == "UP"
                else f"EMA {fast_p}/{slow_p} bullish alignment"
            )
            return f"AUTO: {detail} — closing {pos_action.replace('_', ' ').title()}."
        if pos_action in _BULLISH_ACTIONS and (cross == "DOWN" or aligned == "bear"):
            detail = (
                f"EMA {fast_p}/{slow_p} bearish cross"
                if cross == "DOWN"
                else f"EMA {fast_p}/{slow_p} bearish alignment"
            )
            return f"AUTO: {detail} — closing {pos_action.replace('_', ' ').title()}."

    if intelligent:
        cross = str(sig.get("ema_cross") or "").upper()
        if pos_action == "SELL_IRON_CONDOR":
            if cross in {"UP", "DOWN"}:
                return "AUTO: EMA cross — closing iron condor."
            if bias and bias != "SIDEWAYS":
                return f"AUTO: CPR {bias} — closing iron condor (range ended)."
        if entry_mode == "cpr_trend":
            if bias == "TRENDING_BULL" and pos_action in _BEARISH_ACTIONS:
                return f"AUTO: CPR {bias} — closing bearish {pos_action.replace('_', ' ').title()}."
            if bias == "TRENDING_BEAR" and pos_action in _BULLISH_ACTIONS:
                return f"AUTO: CPR {bias} — closing bullish {pos_action.replace('_', ' ').title()}."

    if fresh != "NO_TRADE" and fresh != pos_action:
        if pos_action in _BEARISH_ACTIONS and fresh in _BULLISH_ACTIONS:
            return f"Strategy signal {fresh} — closing {pos_action.replace('_', ' ').title()}."
        if pos_action in _BULLISH_ACTIONS and fresh in _BEARISH_ACTIONS:
            return f"Strategy signal {fresh} — closing {pos_action.replace('_', ' ').title()}."
        if pos_action == "SELL_IRON_CONDOR" and fresh in _BULLISH_ACTIONS | _BEARISH_ACTIONS:
            return f"Directional signal {fresh} — closing iron condor."

    if bias == "TRENDING_BULL" and pos_action in _BEARISH_ACTIONS:
        return f"CPR regime {bias} — closing bearish {pos_action.replace('_', ' ').title()}."
    if bias == "TRENDING_BEAR" and pos_action in _BULLISH_ACTIONS:
        return f"CPR regime {bias} — closing bullish {pos_action.replace('_', ' ').title()}."
    if (
        bias == "SIDEWAYS"
        and pos_action == "SELL_IRON_CONDOR"
        and fresh in _BULLISH_ACTIONS | _BEARISH_ACTIONS
    ):
        return f"CPR regime {bias} with directional signal — closing iron condor."

    if params.require_ema_cross_for_credit and not intelligent:
        return None

    return None
