"""Multi-leg credit spread metrics, MTM, and spread-aware exits."""

from __future__ import annotations

from typing import Any

from index_ai.config import RiskSettings
from index_ai.instruments import IndexInstrument, get_instrument
from index_ai.strategy_params import StrategyParams, get_strategy_params

CREDIT_ACTIONS = frozenset(
    {
        "SELL_IRON_CONDOR",
        "SELL_BULL_PUT_SPREAD",
        "SELL_BEAR_CALL_SPREAD",
    }
)

CREDIT_EXIT_MODE = "credit_spread"


def is_credit_option(option: dict[str, Any]) -> bool:
    legs = option.get("legs") or []
    if len(legs) >= 2:
        return True
    structure = str(option.get("structure") or "").upper()
    return structure.endswith("_SPREAD") or structure == "IRON_CONDOR"


def is_credit_action(action: str) -> bool:
    return str(action or "").upper() in CREDIT_ACTIONS


def net_credit_points(legs: list[dict[str, Any]]) -> float:
    """Premium received at entry (points per unit, before lot multiplier)."""
    credit = 0.0
    for leg in legs:
        ltp = float(leg.get("ltp") or 0)
        if str(leg.get("transaction_type") or "").upper() == "SELL":
            credit += ltp
        else:
            credit -= ltp
    return max(0.0, round(credit, 2))


def mark_to_close_debit(legs: list[dict[str, Any]], leg_ltps: list[float]) -> float:
    """Cost to close the spread now (points per unit)."""
    debit = 0.0
    for leg, ltp in zip(legs, leg_ltps):
        px = float(ltp)
        if str(leg.get("transaction_type") or "").upper() == "SELL":
            debit += px
        else:
            debit -= px
    return max(0.0, round(debit, 2))


def spread_pnl_rupees(*, entry_credit: float, close_debit: float, quantity: int) -> float:
    qty = max(1, int(quantity))
    return round((float(entry_credit) - float(close_debit)) * qty, 2)


def _spread_width_points(legs: list[dict[str, Any]], option_type: str) -> float:
    strikes = sorted(
        float(leg["strike"])
        for leg in legs
        if str(leg.get("option_type") or "").upper() == option_type.upper()
    )
    if len(strikes) < 2:
        return 0.0
    return max(strikes) - min(strikes)


def max_loss_points(legs: list[dict[str, Any]], structure: str, entry_credit: float) -> float:
    """Defined-risk max loss per unit (index points) for the structure."""
    structure = str(structure or "").upper()
    call_w = _spread_width_points(legs, "CALL")
    put_w = _spread_width_points(legs, "PUT")
    if structure == "IRON_CONDOR":
        wing = max(call_w, put_w)
    elif structure == "BULL_PUT_SPREAD":
        wing = put_w
    elif structure == "BEAR_CALL_SPREAD":
        wing = call_w
    else:
        wing = max(call_w, put_w)
    return max(0.0, round(wing - entry_credit, 2))


def attach_credit_risk_metrics(option: dict[str, Any], instrument: IndexInstrument) -> dict[str, Any]:
    legs = list(option.get("legs") or [])
    if not legs:
        return option
    credit = net_credit_points(legs)
    structure = str(option.get("structure") or "")
    max_loss_pts = max_loss_points(legs, structure, credit)
    qty = int(option.get("quantity") or instrument.lot_size)
    option = {
        **option,
        "ltp": credit,
        "net_credit_points": credit,
        "max_loss_points": max_loss_pts,
        "max_profit_rupees": round(credit * qty, 2),
        "max_loss_rupees": round(max_loss_pts * qty, 2),
    }
    return option


def _short_strikes(legs: list[dict[str, Any]]) -> dict[str, float]:
    shorts: dict[str, float] = {}
    for leg in legs:
        if str(leg.get("transaction_type") or "").upper() != "SELL":
            continue
        side = str(leg.get("option_type") or "").upper()
        if side in {"CALL", "PUT"}:
            shorts[side] = float(leg["strike"])
    return shorts


def init_credit_trail_meta(
    *,
    option: dict[str, Any],
    instrument: IndexInstrument,
    action: str,
    entry_index_price: float,
    params: StrategyParams | None = None,
) -> dict[str, Any]:
    cfg = params or get_strategy_params()
    legs = list(option.get("legs") or [])
    credit = float(option.get("net_credit_points") or option.get("ltp") or net_credit_points(legs))
    max_loss_pts = float(
        option.get("max_loss_points") or max_loss_points(legs, str(option.get("structure") or ""), credit)
    )
    qty = int(option.get("quantity") or instrument.lot_size)
    max_profit = round(credit * qty, 2)
    max_loss = round(max_loss_pts * qty, 2)
    profit_target = float(getattr(cfg, "credit_profit_target_pct", 0.50))
    stop_pct = float(getattr(cfg, "credit_stop_loss_pct", 0.60))
    shorts = _short_strikes(legs)
    return {
        "exit_mode": CREDIT_EXIT_MODE,
        "entry_index_price": entry_index_price,
        "entry_net_credit": credit,
        "max_loss_points": max_loss_pts,
        "max_profit_rupees": max_profit,
        "max_loss_rupees": max_loss,
        "profit_target_rupees": round(max_profit * profit_target, 2),
        "stop_loss_rupees": round(max_loss * stop_pct, 2),
        "profit_target_pct": profit_target,
        "stop_loss_pct": stop_pct,
        "short_call_strike": shorts.get("CALL"),
        "short_put_strike": shorts.get("PUT"),
        "instrument": instrument.key,
        "structure": option.get("structure"),
        "action": action,
        "last_mtm_pnl": None,
        "last_close_debit": None,
    }


def fetch_leg_ltps(client: Any, legs: list[dict[str, Any]]) -> list[float]:
    from index_ai.exit import option_ltp_with_retry

    return [option_ltp_with_retry(client, leg, attempts=2) for leg in legs]


def compute_credit_mtm(
    option: dict[str, Any],
    client: Any,
) -> tuple[float, float, list[float]]:
    """Return (mtm_rupees, close_debit_points, leg_ltps)."""
    legs = list(option.get("legs") or [])
    if not legs:
        raise RuntimeError("Credit spread has no legs.")
    leg_ltps = fetch_leg_ltps(client, legs)
    debit = mark_to_close_debit(legs, leg_ltps)
    credit = float(option.get("net_credit_points") or option.get("ltp") or net_credit_points(legs))
    qty = int(option.get("quantity") or 1)
    pnl = spread_pnl_rupees(entry_credit=credit, close_debit=debit, quantity=qty)
    return pnl, debit, leg_ltps


def format_legs_summary(legs: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for leg in legs:
        tx = str(leg.get("transaction_type") or "BUY").upper()
        side = str(leg.get("option_type") or "").upper()
        strike = leg.get("strike")
        strike_s = str(int(strike)) if strike is not None and float(strike) == int(strike) else f"{strike:g}"
        rows.append(
            {
                "label": f"{'Sell' if tx == 'SELL' else 'Buy'} {strike_s} {side[:2] if side else ''}".strip(),
                "transaction_type": tx,
                "option_type": side,
                "strike_display": strike_s,
            }
        )
    return rows


def evaluate_credit_open_trade(
    trade: dict[str, Any],
    current_index_price: float,
    risk: RiskSettings,
    *,
    fresh_supertrend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exit credit spreads on PnL targets, short-strike breach, or EOD (not index-point trail)."""
    _ = risk
    _ = fresh_supertrend
    option = dict(trade.get("option") or {})
    signal = trade.get("signal") or {}
    action = str(trade.get("action") or signal.get("action") or "")
    meta = dict(option.get("trail_meta") or {})
    if meta.get("exit_mode") != CREDIT_EXIT_MODE:
        inst = get_instrument(str(trade.get("instrument") or option.get("instrument") or "NIFTY"))
        meta = init_credit_trail_meta(
            option=option,
            instrument=inst,
            action=action,
            entry_index_price=float(signal.get("price") or current_index_price),
        )

    mtm = option.get("mtm_pnl")
    if mtm is not None:
        meta["last_mtm_pnl"] = float(mtm)
    close_debit = option.get("last_close_debit")
    if close_debit is not None:
        meta["last_close_debit"] = float(close_debit)

    should_exit = False
    exit_reason: str | None = None

    profit_target = float(meta.get("profit_target_rupees") or 0)
    stop_loss = float(meta.get("stop_loss_rupees") or 0)
    max_loss = float(meta.get("max_loss_rupees") or 0)

    if mtm is not None:
        pnl = float(mtm)
        if profit_target > 0 and pnl >= profit_target:
            should_exit = True
            exit_reason = (
                f"Credit profit target hit: ₹{pnl:,.0f} "
                f"(≥ {int(float(meta.get('profit_target_pct') or 0.5) * 100)}% of max profit)."
            )
        elif stop_loss > 0 and pnl <= -stop_loss:
            should_exit = True
            exit_reason = (
                f"Credit stop loss: ₹{pnl:,.0f} "
                f"(≥ {int(float(meta.get('stop_loss_pct') or 0.6) * 100)}% of defined max loss)."
            )
        elif max_loss > 0 and pnl <= -max_loss:
            should_exit = True
            exit_reason = f"Credit max loss reached: ₹{pnl:,.0f}."

    shorts = _short_strikes(list(option.get("legs") or []))
    if not should_exit and is_credit_action(action):
        if action == "SELL_BULL_PUT_SPREAD":
            sp = shorts.get("PUT")
            if sp is not None and current_index_price < sp:
                should_exit = True
                exit_reason = f"Index {current_index_price:g} below short put {sp:g}."
        elif action == "SELL_BEAR_CALL_SPREAD":
            sc = shorts.get("CALL")
            if sc is not None and current_index_price > sc:
                should_exit = True
                exit_reason = f"Index {current_index_price:g} above short call {sc:g}."
        elif action == "SELL_IRON_CONDOR":
            sc = shorts.get("CALL")
            sp = shorts.get("PUT")
            if sc is not None and current_index_price > sc:
                should_exit = True
                exit_reason = f"Iron condor: index {current_index_price:g} above short call {sc:g}."
            elif sp is not None and current_index_price < sp:
                should_exit = True
                exit_reason = f"Iron condor: index {current_index_price:g} below short put {sp:g}."

    return {
        "trade_id": trade.get("id"),
        "instrument": trade.get("instrument"),
        "action": action,
        "transaction_type": "SELL",
        "current_index_price": current_index_price,
        "trail": meta,
        "should_exit": should_exit,
        "exit_reason": exit_reason,
        "supertrend_exit": False,
        "credit_exit": should_exit,
    }
