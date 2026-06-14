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
    legs = list(option.get("legs") or [])
    if len(legs) >= 2 and all(leg.get("security_id") is not None for leg in legs):
        return True
    structure = str(option.get("structure") or "").upper()
    return False


def is_credit_action(action: str) -> bool:
    return str(action or "").upper() in CREDIT_ACTIONS


def credit_spread_entry_ready(option: dict[str, Any], *, action: str = "") -> tuple[bool, str]:
    """Require full Dhan legs before journal or broker entry."""
    act = str(action or "").upper()
    structure = str(option.get("structure") or "").upper()
    if act not in CREDIT_ACTIONS and not (
        structure.endswith("_SPREAD") or structure == "IRON_CONDOR"
    ):
        return True, "not a credit spread"
    legs = list(option.get("legs") or [])
    min_legs = 4 if structure == "IRON_CONDOR" or act == "SELL_IRON_CONDOR" else 2
    if len(legs) < min_legs:
        return False, f"Credit spread needs {min_legs} legs (got {len(legs)})."
    for idx, leg in enumerate(legs, start=1):
        if leg.get("security_id") is None:
            return False, f"Leg {idx} missing security_id — option chain snapshot incomplete."
        if not str(leg.get("transaction_type") or "").upper():
            return False, f"Leg {idx} missing buy/sell side."
        if leg.get("strike") is None:
            return False, f"Leg {idx} missing strike."
    return True, "ok"


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
    supertrend_direction: int = 0,
    supertrend_stop: float = 0.0,
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
    from index_ai.profit_trail import attach_profit_trail_meta

    meta = {
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
        "supertrend_direction": int(supertrend_direction),
        "supertrend_stop": float(supertrend_stop or 0),
        "last_mtm_pnl": None,
        "last_close_debit": None,
    }
    return attach_profit_trail_meta(meta, params=cfg)


def _parse_ltp_bucket(raw: dict[str, Any], segment: str, security_id: int) -> float | None:
    from index_ai.exit import _parse_ltp_from_feed

    return _parse_ltp_from_feed(raw, segment, security_id)


def fetch_leg_ltps(
    client: Any,
    option: dict[str, Any],
    *,
    instrument_key: str | None = None,
    ltp_cache: dict[tuple[str, int], float] | None = None,
) -> list[float]:
    """Fetch live LTP per leg (batch marketfeed, then option-chain fallback)."""
    legs = list(option.get("legs") or [])
    if not legs:
        return []

    segment = str(legs[0].get("segment") or "NSE_FNO")
    ids = [int(leg["security_id"]) for leg in legs if leg.get("security_id") is not None]
    if not ids:
        return []
    prices: dict[int, float] = {}
    if ltp_cache:
        for leg in legs:
            if leg.get("security_id") is None:
                continue
            sid = int(leg["security_id"])
            cached = ltp_cache.get((segment, sid))
            if cached is not None:
                prices[sid] = cached

    missing_ids = [sid for sid in ids if sid not in prices]
    if missing_ids:
        try:
            raw = client.ltp(segment, missing_ids)
            for sid in missing_ids:
                px = _parse_ltp_bucket(raw, segment, sid)
                if px is not None:
                    prices[sid] = px
        except Exception:
            pass

    missing = [
        leg
        for leg in legs
        if leg.get("security_id") is not None and int(leg["security_id"]) not in prices
    ]
    if missing:
        key = instrument_key or str(option.get("instrument") or "")
        if key:
            try:
                from index_ai.dhan import DhanClient
                from index_ai.instruments import get_instrument
                from index_ai.options_expiry import resolve_trade_expiry

                inst = get_instrument(key)
                expiry = resolve_trade_expiry(option, client, inst)
                if expiry:
                    chain = client.option_chain(inst, expiry)
                    rows = (chain.get("data") or {}).get("oc") or {}
                    for leg in missing:
                        strike = leg.get("strike")
                        side = "ce" if str(leg.get("option_type") or "").upper() == "CALL" else "pe"
                        row = rows.get(str(strike)) or (
                            rows.get(str(int(strike))) if strike is not None else {}
                        )
                        if not row and strike is not None and rows:
                            nearest = min(rows.keys(), key=lambda k: abs(float(k) - float(strike)))
                            row = rows.get(nearest) or {}
                        leg_row = row.get(side) or {}
                        ltp = leg_row.get("last_price") or leg_row.get("ltp")
                        if ltp is not None:
                            prices[int(leg["security_id"])] = float(ltp)
            except Exception:
                pass

    out: list[float] = []
    for leg in legs:
        if leg.get("security_id") is None:
            continue
        sid = int(leg["security_id"])
        if sid in prices:
            out.append(prices[sid])
            continue
        from index_ai.exit import option_ltp_with_retry

        out.append(option_ltp_with_retry(client, leg, attempts=1))
    return out


def sync_leg_current_ltps(option: dict[str, Any], leg_ltps: list[float]) -> None:
    legs = list(option.get("legs") or [])
    for leg, px in zip(legs, leg_ltps):
        leg["entry_ltp"] = leg.get("entry_ltp", leg.get("ltp"))
        leg["current_ltp"] = float(px)
    option["legs"] = legs


def compute_credit_mtm(
    option: dict[str, Any],
    client: Any,
    *,
    instrument_key: str | None = None,
    ltp_cache: dict[tuple[str, int], float] | None = None,
) -> tuple[float, float, list[float]]:
    """Return (mtm_rupees, close_debit_points, leg_ltps)."""
    legs = list(option.get("legs") or [])
    if not legs:
        raise RuntimeError("Credit spread has no legs.")
    leg_ltps = fetch_leg_ltps(
        client, option, instrument_key=instrument_key, ltp_cache=ltp_cache
    )
    sync_leg_current_ltps(option, leg_ltps)
    legs = list(option.get("legs") or [])
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
    from index_ai.trailing import check_supertrend_exit

    _ = risk
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
        from index_ai.profit_trail import evaluate_profit_trail

        meta, profit_hit, profit_reason = evaluate_profit_trail(meta, pnl)
        if profit_hit and profit_reason:
            should_exit = True
            exit_reason = profit_reason
        elif (
            not meta.get("use_profit_trail")
            and profit_target > 0
            and pnl >= profit_target
        ):
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

    if not should_exit:
        st_hit, st_reason = check_supertrend_exit(meta, current_index_price, fresh_supertrend)
        if st_hit and st_reason:
            should_exit = True
            exit_reason = st_reason

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
