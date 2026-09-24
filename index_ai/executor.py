from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from index_ai.brain.regime import TIGHTEN_SELL_STOP_KEY
from index_ai.config import AppSettings
from index_ai.dhan import DhanClient
from index_ai.instruments import IndexInstrument, get_instrument
from index_ai.learning import learned_settings, loss_guard_for_setup, record_trade
from index_ai.hf_learning import build_setup_narrative, score_setup_hf
from index_ai.ml_outcomes import extract_features, score_trade_setup
from index_ai.market_clock import now_ist_iso
from index_ai.strategies.credit_spread import CREDIT_ACTIONS
from index_ai.strategies.premium_sell import is_premium_sell_action
from index_ai.risk import check_execution_gates
from index_ai.strategies.strategy import StrategySignal
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.strategies.credit_spread import init_credit_trail_meta, is_credit_option
from index_ai.execution_safety import acquire_execution_lock, validate_execution_plan
from index_ai.trailing import init_trail_meta


@dataclass(frozen=True)
class ExecutionPlan:
    allowed: bool
    mode: str
    reason: str
    option: dict[str, Any] | None
    signal: dict[str, Any]


def _min_confidence_gate(
    signal_action: str, app_settings: AppSettings, learned: dict[str, Any]
) -> float:
    """Buy setups use learned gate; credit spreads use CPR credit floor (not buy-tuned learning)."""
    from index_ai.pre_open_brief import entry_confidence_bump
    from index_ai.risk_policy import HARDCODED_RISK

    if signal_action in CREDIT_ACTIONS or is_premium_sell_action(signal_action):
        base = get_strategy_params().credit_confidence_gate
    else:
        base = float(
            learned.get("effective_min_confidence")
            or (
                app_settings.risk.min_confidence
                + float(learned.get("min_confidence_adjustment") or 0)
            )
        )
        # Buy floor — the learned adjustment can loosen but never below the 60% bar.
        base = max(base, HARDCODED_RISK.min_confidence)
    return base + entry_confidence_bump()


def build_execution_plan(
    *,
    app_settings: AppSettings,
    instrument: IndexInstrument,
    signal: StrategySignal,
    option: dict[str, Any] | None,
) -> ExecutionPlan:
    learned = learned_settings()
    signal_action = str(signal.action)
    min_conf = _min_confidence_gate(signal_action, app_settings, learned)
    if option is None:
        return ExecutionPlan(
            False, app_settings.risk.trading_mode, "No option selected.", option, signal.to_dict()
        )
    tx = str(option.get("transaction_type") or "BUY").upper()
    ok, reason = check_execution_gates(
        risk=app_settings.risk,
        signal_action=signal_action,
        transaction_type=tx,
        confidence=signal.confidence,
        min_confidence=min_conf,
        strategy_mode=str(signal.strategy_mode or ""),
    )
    if not ok:
        mode = (
            "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
        )
        return ExecutionPlan(False, mode, reason, option, signal.to_dict())

    loss_guard = loss_guard_for_setup(instrument.key, signal_action)
    if loss_guard.get("blocked"):
        mode = (
            "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
        )
        guarded_signal = {
            **signal.to_dict(),
            "loss_guard": loss_guard,
        }
        return ExecutionPlan(False, mode, str(loss_guard["reason"]), option, guarded_signal)

    safety = validate_execution_plan(
        app_settings=app_settings,
        instrument=instrument,
        signal=signal.to_dict(),
        option=option,
        min_confidence=min_conf,
    )
    if not safety.ok:
        mode = (
            "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
        )
        return ExecutionPlan(False, mode, safety.reason, option, signal.to_dict())

    scoring_signal = {**signal.to_dict(), "signal_time": now_ist_iso()}
    ml = score_trade_setup(scoring_signal, option, instrument.key)
    if ml.get("ready") and ml.get("win_probability") is not None:
        gate = max(
            float(learned.get("ml_min_win_prob") or 0.0),
            float(ml.get("min_win_prob_gate") or 0.52),
        )
        # Per-lane bounds on the model's win-prob gate. Sell setups sit in a
        # band (a credit spread is meant to win small and often, so the gate
        # should not chase 70%+); buy setups only need a floor.
        _sp = get_strategy_params()
        if signal_action in CREDIT_ACTIONS or is_premium_sell_action(signal_action):
            gate = min(max(gate, _sp.ml_gate_sell_min), _sp.ml_gate_sell_max)
        else:
            gate = max(gate, _sp.ml_gate_buy_min)
        win_p = float(ml["win_probability"])
        if bool(ml.get("gate_active")) and win_p < gate:
            mode = (
                "LIVE"
                if app_settings.risk.trading_mode == "LIVE"
                else app_settings.risk.trading_mode
            )
            return ExecutionPlan(
                False,
                mode,
                (
                    f"ML model v{ml.get('model_version')} predicts {win_p:.0%} win chance "
                    f"(gate {gate:.0%}). Setup blocked until more winning patterns are learned."
                ),
                option,
                signal.to_dict(),
            )

    if signal_action in CREDIT_ACTIONS or is_premium_sell_action(signal_action):
        ml_note = ""
        if ml.get("ready") and ml.get("win_probability") is not None:
            state = "enforced" if ml.get("gate_active") else "scoring only"
            ml_note = f" ML win estimate {float(ml['win_probability']):.0%} ({state})."
        return ExecutionPlan(
            True,
            app_settings.risk.trading_mode,
            f"Credit structure passed gates (min confidence {min_conf:.0%}).{ml_note}",
            option,
            signal.to_dict(),
        )

    hf = score_setup_hf(signal.to_dict(), option, instrument.key)
    if hf.get("ready") and hf.get("block_setup"):
        mode = (
            "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
        )
        return ExecutionPlan(
            False,
            mode,
            (
                f"Hugging Face {hf.get('model')} sentiment {hf.get('label')} "
                f"(positive {float(hf.get('positive_prob') or 0):.0%}) — "
                "setup blocked by FinBERT gate."
            ),
            option,
            signal.to_dict(),
        )

    return ExecutionPlan(True, app_settings.risk.trading_mode, reason, option, signal.to_dict())


def execute_plan(
    plan: ExecutionPlan,
    settings: AppSettings,
    client: DhanClient,
    *,
    instrument: IndexInstrument | None = None,
) -> dict[str, Any]:
    from index_ai.config import settings as load_settings
    from index_ai.dhan_orders import attach_broker_orders

    fresh = load_settings()
    trade_mode = str(fresh.risk.trading_mode or plan.mode or "PAPER").upper()

    if not plan.allowed or not plan.option:
        return {"status": "BLOCKED", "reason": plan.reason, "plan": plan.__dict__}

    inst = instrument or get_instrument(str(plan.option.get("instrument") or "NIFTY"))
    learned = learned_settings()
    min_conf = _min_confidence_gate(str(plan.signal.get("action") or ""), fresh, learned)
    safety = validate_execution_plan(
        app_settings=fresh,
        instrument=inst,
        signal=plan.signal,
        option=plan.option,
        min_confidence=min_conf,
    )
    if not safety.ok:
        return {
            "status": "BLOCKED",
            "reason": safety.reason,
            "safety_code": safety.code,
            "plan": plan.__dict__,
        }

    instrument_key = str(plan.option.get("instrument") or inst.key)
    lock = acquire_execution_lock(instrument_key)
    status = "PAPER_RECORDED"
    broker_response: dict[str, Any] | None = None

    with lock:
        safety = validate_execution_plan(
            app_settings=fresh,
            instrument=inst,
            signal=plan.signal,
            option=plan.option,
            min_confidence=min_conf,
        )
        if not safety.ok:
            return {
                "status": "BLOCKED",
                "reason": safety.reason,
                "safety_code": safety.code,
                "plan": plan.__dict__,
            }

        if trade_mode == "LIVE":
            from index_ai.dhan_orders import (
                attach_broker_orders,
                live_orders_enabled,
                place_live_entry_orders,
            )

            if not live_orders_enabled(fresh):
                return {
                    "status": "BLOCKED",
                    "reason": (
                        "Live orders are not enabled. Confirm Live mode, Dhan token, and kill switch."
                    ),
                    "plan": plan.__dict__,
                }
            try:
                broker_response = place_live_entry_orders(
                    client,
                    plan.option,
                    settings=fresh,
                    signal_action=str(plan.signal.get("action") or ""),
                )
                status = str(broker_response.get("status") or "LIVE_SENT")
            except Exception as exc:
                return {"status": "LIVE_FAILED", "reason": str(exc), "plan": plan.__dict__}

        option_payload = (
            attach_broker_orders(dict(plan.option), broker_response)
            if broker_response
            else dict(plan.option)
        )
        for leg in option_payload.get("legs") or []:
            if leg.get("ltp") is not None and leg.get("entry_ltp") is None:
                leg["entry_ltp"] = leg["ltp"]
        if option_payload.get("ltp") is not None and option_payload.get("entry_ltp") is None:
            option_payload["entry_ltp"] = option_payload["ltp"]
        option_payload.setdefault("signal_time", now_ist_iso())
        option_payload["ml_features"] = extract_features(plan.signal, option_payload, inst.key)
        ml_snap = score_trade_setup(plan.signal, option_payload, inst.key)
        if ml_snap.get("win_probability") is not None:
            option_payload["ml_win_probability"] = ml_snap["win_probability"]
            option_payload["ml_gate_active"] = bool(ml_snap.get("gate_active"))
        hf_snap = score_setup_hf(plan.signal, option_payload, inst.key)
        if hf_snap.get("ready"):
            option_payload["hf_sentiment"] = {
                "label": hf_snap.get("label"),
                "positive_prob": hf_snap.get("positive_prob"),
                "negative_prob": hf_snap.get("negative_prob"),
                "model": hf_snap.get("model"),
            }
        option_payload["setup_narrative"] = build_setup_narrative(
            plan.signal, option_payload, inst.key
        )
        if is_credit_option(option_payload):
            # Dhan's own margin for this spread (hedge benefit included) and for
            # the sold leg alone, saved for the trade log. Best effort: a failed
            # margin lookup never blocks or delays the entry decision.
            try:
                sells = [
                    lg for lg in option_payload.get("legs") or []
                    if str(lg.get("transaction_type") or "").upper() == "SELL"
                ]
                qty_m = int(option_payload.get("quantity") or inst.lot_size)
                option_payload["margin_dhan"] = {
                    **client.basket_margin(option_payload["legs"], qty_m),
                    "sell_leg_alone": client.basket_margin(sells, qty_m)["total"],
                }
            except Exception:
                pass
            credit_params = get_strategy_params()
            if plan.signal.get(TIGHTEN_SELL_STOP_KEY):
                # HIGH_VOL day (big prior-day range or opening gap) — still sell,
                # just on a shorter leash than the normal stop.
                credit_params = replace(
                    credit_params, credit_stop_loss_pct=credit_params.credit_stop_loss_pct_high_vol
                )
            option_payload["trail_meta"] = init_credit_trail_meta(
                option=option_payload,
                instrument=inst,
                action=str(plan.signal["action"]),
                entry_index_price=float(plan.signal["price"]),
                params=credit_params,
                supertrend_direction=int(plan.signal.get("supertrend_direction") or 0),
                supertrend_stop=float(plan.signal.get("supertrend_stop") or 0),
            )
        else:
            option_payload["trail_meta"] = init_trail_meta(
                entry_index_price=float(plan.signal["price"]),
                action=str(plan.signal["action"]),
                transaction_type=str(plan.option.get("transaction_type") or "BUY"),
                instrument=inst,
                supertrend_direction=int(plan.signal.get("supertrend_direction") or 0),
                supertrend_stop=float(plan.signal.get("supertrend_stop") or 0),
            )
        trade_id = record_trade(
            mode=trade_mode,
            instrument=str(plan.option["instrument"]),
            action=str(plan.signal["action"]),
            confidence=float(plan.signal["confidence"]),
            option=option_payload,
            signal=plan.signal,
            status=status,
        )
        if "REJECT" not in status.upper():
            try:
                from index_ai.notify import trade_opened

                trade_opened(
                    instrument=str(plan.option["instrument"]),
                    action=str(plan.signal["action"]),
                    mode=trade_mode,
                    option=option_payload,
                    trade_id=trade_id,
                )
            except Exception:
                pass
        return {
            "status": status,
            "trade_id": trade_id,
            "broker_response": broker_response,
            "plan": plan.__dict__,
        }
