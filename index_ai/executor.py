from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from index_ai.config import AppSettings
from index_ai.dhan import DhanClient
from index_ai.instruments import IndexInstrument, get_instrument
from index_ai.learning import learned_settings, record_trade
from index_ai.hf_learning import build_setup_narrative, score_setup_hf
from index_ai.ml_outcomes import extract_features, score_trade_setup
from index_ai.option_structures import CREDIT_ACTIONS
from index_ai.risk import check_execution_gates
from index_ai.strategy import StrategySignal
from index_ai.strategy_params import get_strategy_params
from index_ai.trailing import init_trail_meta


@dataclass(frozen=True)
class ExecutionPlan:
    allowed: bool
    mode: str
    reason: str
    option: dict[str, Any] | None
    signal: dict[str, Any]


def _min_confidence_gate(signal_action: str, app_settings: AppSettings, learned: dict[str, Any]) -> float:
    """Buy setups use learned gate; credit spreads use CPR credit floor (not buy-tuned learning)."""
    if signal_action in CREDIT_ACTIONS:
        return get_strategy_params().credit_min_confidence
    return float(
        learned.get("effective_min_confidence")
        or (
            app_settings.risk.min_confidence
            + float(learned.get("min_confidence_adjustment") or 0)
        )
    )


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
        return ExecutionPlan(False, app_settings.risk.trading_mode, "No option selected.", option, signal.to_dict())
    tx = str(option.get("transaction_type") or "BUY").upper()
    ok, reason = check_execution_gates(
        risk=app_settings.risk,
        signal_action=signal_action,
        transaction_type=tx,
        confidence=signal.confidence,
        min_confidence=min_conf,
    )
    if not ok:
        mode = "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
        return ExecutionPlan(False, mode, reason, option, signal.to_dict())

    if signal_action in CREDIT_ACTIONS:
        return ExecutionPlan(
            True,
            app_settings.risk.trading_mode,
            f"Credit structure passed gates (min confidence {min_conf:.0%}).",
            option,
            signal.to_dict(),
        )

    ml = score_trade_setup(signal.to_dict(), option, instrument.key)
    if ml.get("ready") and ml.get("win_probability") is not None:
        gate = float(learned.get("ml_min_win_prob") or ml.get("min_win_prob_gate") or 0.45)
        win_p = float(ml["win_probability"])
        if win_p < gate:
            mode = "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
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

    hf = score_setup_hf(signal.to_dict(), option, instrument.key)
    if hf.get("ready") and hf.get("block_setup"):
        mode = "LIVE" if app_settings.risk.trading_mode == "LIVE" else app_settings.risk.trading_mode
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
    if not plan.allowed or not plan.option:
        return {"status": "BLOCKED", "reason": plan.reason, "plan": plan.__dict__}
    status = "PAPER_RECORDED"
    broker_response: dict[str, Any] | None = None
    if plan.mode == "LIVE":
        legs = list(plan.option.get("legs") or [])
        if legs:
            broker_responses: list[dict[str, Any]] = []
            base_id = uuid.uuid4().hex[:10]
            for idx, leg in enumerate(legs):
                broker_responses.append(
                    client.place_market_order(
                        security_id=int(leg["security_id"]),
                        exchange_segment=str(leg["segment"]),
                        transaction_type=str(leg["transaction_type"]),
                        quantity=int(leg.get("quantity") or plan.option["quantity"]),
                        correlation_id=f"idxai-{base_id}-{idx}"[:30],
                    )
                )
            broker_response = {"legs": broker_responses}
            status = "LIVE_SENT"
        else:
            broker_response = client.place_market_order(
                security_id=int(plan.option["security_id"]),
                exchange_segment=str(plan.option["segment"]),
                transaction_type=str(plan.option["transaction_type"]),
                quantity=int(plan.option["quantity"]),
                correlation_id=f"idxai-{uuid.uuid4().hex[:12]}",
            )
            status = str(broker_response.get("orderStatus") or "LIVE_SENT")
    inst = instrument or get_instrument(str(plan.option.get("instrument") or "NIFTY"))
    option_payload = dict(plan.option)
    option_payload["ml_features"] = extract_features(
        plan.signal, option_payload, inst.key
    )
    ml_snap = score_trade_setup(plan.signal, option_payload, inst.key)
    if ml_snap.get("win_probability") is not None:
        option_payload["ml_win_probability"] = ml_snap["win_probability"]
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
    option_payload["trail_meta"] = init_trail_meta(
        entry_index_price=float(plan.signal["price"]),
        action=str(plan.signal["action"]),
        transaction_type=str(plan.option.get("transaction_type") or "BUY"),
        instrument=inst,
        supertrend_direction=int(plan.signal.get("supertrend_direction") or 0),
        supertrend_stop=float(plan.signal.get("supertrend_stop") or 0),
    )
    trade_id = record_trade(
        mode=plan.mode,
        instrument=str(plan.option["instrument"]),
        action=str(plan.signal["action"]),
        confidence=float(plan.signal["confidence"]),
        option=option_payload,
        signal=plan.signal,
        status=status,
    )
    return {"status": status, "trade_id": trade_id, "broker_response": broker_response, "plan": plan.__dict__}
