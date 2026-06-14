"""Pre-trade validation — blocks journal rows and broker orders when anything is unsafe."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from index_ai.config import AppSettings
from index_ai.credit_spread import CREDIT_ACTIONS, credit_spread_entry_ready, is_credit_action
from index_ai.premium_sell import PREMIUM_SELL_ACTIONS, is_premium_sell_action, premium_sell_entry_ready
from index_ai.instruments import IndexInstrument, get_instrument
from index_ai.learning import is_broker_filled_open, open_trades_for_mode
from index_ai.risk import check_execution_gates, kill_switch_state
from index_ai.strategy import StrategySignal
from index_ai.strategy_params import get_strategy_params
from index_ai.trade_lots import MAX_LOTS_PER_TRADE, MIN_LOTS_PER_TRADE, order_quantity

_INSTRUMENT_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_EXECUTE_LOCK = threading.Lock()

_BULLISH_CREDIT = frozenset({"SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"})
_BEARISH_CREDIT = frozenset({"SELL_BEAR_CALL_SPREAD", "SELL_ATM_CALL"})
_LIVE_ENTRY_OK = frozenset({"LIVE_TRADED"})
_LIVE_EXIT_OK = frozenset({"LIVE_TRADED"})


@dataclass(frozen=True)
class SafetyCheck:
    ok: bool
    reason: str
    code: str = "ok"


def signal_from_payload(payload: dict[str, Any]) -> StrategySignal:
    """Build StrategySignal from API JSON without breaking on unknown keys."""
    allowed = StrategySignal.__dataclass_fields__
    data: dict[str, Any] = {
        "action": "NO_TRADE",
        "reason": "",
        "confidence": 0.0,
        "price": 0.0,
        "pivot": 0.0,
        "bc": 0.0,
        "tc": 0.0,
        "ema_fast": 0.0,
        "ema_slow": 0.0,
    }
    data.update({k: payload[k] for k in payload if k in allowed})
    return StrategySignal(**data)  # type: ignore[arg-type]


def _instrument_lock(instrument_key: str) -> threading.Lock:
    key = instrument_key.upper()
    if key not in _INSTRUMENT_LOCKS:
        with _GLOBAL_EXECUTE_LOCK:
            if key not in _INSTRUMENT_LOCKS:
                _INSTRUMENT_LOCKS[key] = threading.Lock()
    return _INSTRUMENT_LOCKS[key]


def validate_strategy_coherence(signal: dict[str, Any], action: str) -> SafetyCheck:
    """Block trades the intelligent router marked as conflict / wait."""
    mode = str(signal.get("strategy_mode") or "")
    if mode == "conflict":
        return SafetyCheck(
            False,
            "Strategy conflict (CPR vs EMA) — execution blocked.",
            "strategy_conflict",
        )
    if mode == "wait" and action in CREDIT_ACTIONS:
        return SafetyCheck(
            False,
            "No confirmed setup (AUTO wait) — credit execution blocked.",
            "strategy_wait",
        )
    if action in _BEARISH_CREDIT and str(signal.get("ema_aligned") or "") == "bull":
        return SafetyCheck(
            False,
            "Bearish credit blocked while spot EMA is bullish.",
            "ema_mismatch",
        )
    if action in _BULLISH_CREDIT and str(signal.get("ema_aligned") or "") == "bear":
        return SafetyCheck(
            False,
            "Bullish credit blocked while spot EMA is bearish.",
            "ema_mismatch",
        )
    return SafetyCheck(True, "ok", "ok")


def validate_action_matches_option(action: str, option: dict[str, Any]) -> SafetyCheck:
    act = str(action or "").upper()
    structure = str(option.get("structure") or "").upper()
    legs = list(option.get("legs") or [])

    if act == "SELL_IRON_CONDOR":
        if structure != "IRON_CONDOR" or len(legs) != 4:
            return SafetyCheck(False, "Iron condor action requires 4 legs on option.", "structure_mismatch")
        sells = [l for l in legs if str(l.get("transaction_type")).upper() == "SELL"]
        if len(sells) != 2:
            return SafetyCheck(False, "Iron condor must have two short legs.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act == "SELL_BULL_PUT_SPREAD":
        if structure != "BULL_PUT_SPREAD" or len(legs) != 2:
            return SafetyCheck(False, "Bull put spread requires 2 legs.", "structure_mismatch")
        short_puts = [
            l
            for l in legs
            if str(l.get("option_type")).upper() == "PUT"
            and str(l.get("transaction_type")).upper() == "SELL"
        ]
        if len(short_puts) != 1:
            return SafetyCheck(False, "Bull put spread must short one put leg.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act == "SELL_BEAR_CALL_SPREAD":
        if structure != "BEAR_CALL_SPREAD" or len(legs) != 2:
            return SafetyCheck(False, "Bear call spread requires 2 legs.", "structure_mismatch")
        short_calls = [
            l
            for l in legs
            if str(l.get("option_type")).upper() == "CALL"
            and str(l.get("transaction_type")).upper() == "SELL"
        ]
        if len(short_calls) != 1:
            return SafetyCheck(False, "Bear call spread must short one call leg.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act == "SELL_ATM_PUT":
        if structure != "ATM_SHORT_PUT" or len(legs) != 1:
            return SafetyCheck(False, "ATM put sell requires one PUT leg.", "structure_mismatch")
        if str(option.get("option_type") or "").upper() != "PUT":
            return SafetyCheck(False, "SELL_ATM_PUT must be PUT.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act == "SELL_ATM_CALL":
        if structure != "ATM_SHORT_CALL" or len(legs) != 1:
            return SafetyCheck(False, "ATM call sell requires one CALL leg.", "structure_mismatch")
        if str(option.get("option_type") or "").upper() != "CALL":
            return SafetyCheck(False, "SELL_ATM_CALL must be CALL.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act in {"BUY_CALL", "BUY_PUT"}:
        if legs:
            return SafetyCheck(False, "Long premium entry must be a single-leg option.", "structure_mismatch")
        side = "CALL" if act == "BUY_CALL" else "PUT"
        if str(option.get("option_type") or "").upper() != side:
            return SafetyCheck(
                False,
                f"Action {act} does not match option type {option.get('option_type')}.",
                "structure_mismatch",
            )
        if str(option.get("transaction_type") or "BUY").upper() != "BUY":
            return SafetyCheck(False, "Long premium must be a BUY order.", "structure_mismatch")
        return SafetyCheck(True, "ok", "ok")

    if act in CREDIT_ACTIONS:
        return SafetyCheck(False, f"Credit action {act} not matched to option structure.", "structure_mismatch")

    return SafetyCheck(True, "ok", "ok")


def validate_quantities(option: dict[str, Any], instrument: IndexInstrument) -> SafetyCheck:
    expected = order_quantity(instrument)
    if expected < instrument.lot_size * MIN_LOTS_PER_TRADE:
        return SafetyCheck(False, "Order quantity below minimum lot.", "qty_invalid")
    if expected > instrument.lot_size * MAX_LOTS_PER_TRADE:
        return SafetyCheck(False, "Order quantity above maximum lots.", "qty_invalid")

    top_qty = int(option.get("quantity") or 0)
    if top_qty and top_qty != expected:
        return SafetyCheck(
            False,
            f"Option quantity {top_qty} != expected {expected} for {instrument.key}.",
            "qty_mismatch",
        )

    legs = list(option.get("legs") or [])
    for idx, leg in enumerate(legs, start=1):
        q = int(leg.get("quantity") or top_qty or 0)
        if q != expected:
            return SafetyCheck(
                False,
                f"Leg {idx} quantity {q} != expected {expected}.",
                "qty_mismatch",
            )
        if q <= 0:
            return SafetyCheck(False, f"Leg {idx} has invalid quantity.", "qty_invalid")

    if not legs:
        sid = option.get("security_id")
        if sid is None:
            return SafetyCheck(False, "Single-leg option missing security_id.", "leg_incomplete")
        if top_qty <= 0:
            return SafetyCheck(False, "Single-leg option missing quantity.", "qty_invalid")

    return SafetyCheck(True, "ok", "ok")


def validate_open_position(instrument_key: str, mode: str) -> SafetyCheck:
    normalized = str(mode or "PAPER").upper()
    for trade in open_trades_for_mode(normalized):
        if str(trade.get("instrument") or "") == instrument_key and trade.get("pnl") is None:
            return SafetyCheck(
                False,
                f"Open {normalized} position already exists for {instrument_key}.",
                "duplicate_open",
            )
    return SafetyCheck(True, "ok", "ok")


def validate_live_entry_allowed(settings: AppSettings) -> SafetyCheck:
    if settings.risk.trading_mode != "LIVE":
        return SafetyCheck(True, "ok", "ok")
    if not settings.dhan.ready:
        return SafetyCheck(False, "Dhan not ready for live orders.", "dhan_not_ready")
    if not settings.risk.allow_live_trading:
        return SafetyCheck(
            False,
            "ALLOW_LIVE_TRADING=false — live broker orders blocked.",
            "live_disabled",
        )
    ks = kill_switch_state(settings.risk)
    if ks["active"]:
        return SafetyCheck(
            False,
            "Kill switch active: " + " ".join(ks["reasons"]),
            "kill_switch",
        )
    return SafetyCheck(True, "ok", "ok")


def validate_live_exit_allowed(trade: dict[str, Any], settings: AppSettings) -> SafetyCheck:
    if str(trade.get("mode") or "").upper() != "LIVE":
        return SafetyCheck(True, "ok", "ok")
    if settings.risk.trading_mode != "LIVE":
        return SafetyCheck(False, "Dashboard not in Live mode — exit orders blocked.", "mode_mismatch")
    live_ok = validate_live_entry_allowed(settings)
    if not live_ok.ok:
        return live_ok
    status = str(trade.get("status") or "").upper()
    if status in {"LIVE_REJECTED", "LIVE_FAILED"}:
        return SafetyCheck(True, "ok", "ok")
    if status not in _LIVE_EXIT_OK:
        return SafetyCheck(
            False,
            f"Live exit blocked — journal status {status or 'unknown'} (need confirmed LIVE_TRADED).",
            "live_not_filled",
        )
    if not is_broker_filled_open(trade):
        return SafetyCheck(
            False,
            "Live exit blocked — broker fills not confirmed for this row.",
            "live_not_filled",
        )
    return SafetyCheck(True, "ok", "ok")


def validate_execution_plan(
    *,
    app_settings: AppSettings,
    instrument: IndexInstrument,
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    min_confidence: float,
) -> SafetyCheck:
    """Full gate used by build_execution_plan and execute_plan."""
    action = str(signal.get("action") or "NO_TRADE").upper()
    if action == "NO_TRADE":
        return SafetyCheck(False, "No trade action.", "no_trade")

    if option is None:
        return SafetyCheck(False, "No option selected.", "no_option")

    inst_key = str(option.get("instrument") or instrument.key)
    if inst_key != instrument.key:
        return SafetyCheck(
            False,
            f"Instrument mismatch: plan {inst_key} vs {instrument.key}.",
            "instrument_mismatch",
        )

    tx = str(option.get("transaction_type") or "BUY").upper()
    ok, reason = check_execution_gates(
        risk=app_settings.risk,
        signal_action=action,
        transaction_type=tx,
        confidence=float(signal.get("confidence") or 0),
        min_confidence=min_confidence,
        strategy_mode=str(signal.get("strategy_mode") or ""),
    )
    if not ok:
        return SafetyCheck(False, reason, "risk_gate")

    for check in (
        validate_strategy_coherence(signal, action),
        validate_action_matches_option(action, option),
        validate_quantities(option, instrument),
        validate_open_position(instrument.key, app_settings.risk.trading_mode),
    ):
        if not check.ok:
            return check

    mode_tag = str(signal.get("strategy_mode") or "")
    if is_premium_sell_action(action) or mode_tag.startswith("apex"):
        if is_premium_sell_action(action):
            prem_ok, prem_reason = premium_sell_entry_ready(option, action=action)
            if not prem_ok:
                return SafetyCheck(False, prem_reason, "premium_not_ready")
        from index_ai.apex_risk import apex_daily_trade_limit_reached

        if apex_daily_trade_limit_reached(instrument.key, app_settings.risk.trading_mode):
            limit = get_strategy_params().apex_max_trades_per_day
            return SafetyCheck(
                False,
                f"Apex max {limit} trades per index today — limit reached.",
                "apex_daily_limit",
            )
    if not is_premium_sell_action(action):
        spread_ok, spread_reason = credit_spread_entry_ready(option, action=action)
        if not spread_ok:
            return SafetyCheck(False, spread_reason, "credit_not_ready")

    if is_credit_action(action):
        params = get_strategy_params()
        if params.auto_intelligent_routing:
            mode = str(signal.get("strategy_mode") or "")
            if mode in {"conflict", "wait"} and action in CREDIT_ACTIONS:
                return SafetyCheck(
                    False,
                    f"Intelligent AUTO blocked credit (mode={mode}).",
                    "strategy_mode",
                )

    live_check = validate_live_entry_allowed(app_settings)
    if app_settings.risk.trading_mode == "LIVE" and not live_check.ok:
        return live_check

    return SafetyCheck(True, "ok", "ok")


def validate_live_order_payload(
    option: dict[str, Any],
    *,
    instrument_key: str,
    action: str,
    settings: AppSettings,
) -> SafetyCheck:
    """Last check immediately before Dhan place_order calls."""
    instrument = get_instrument(instrument_key)
    live_check = validate_live_entry_allowed(settings)
    if not live_check.ok:
        return live_check
    for check in (
        validate_action_matches_option(action, option),
        validate_quantities(option, instrument),
    ):
        if not check.ok:
            return check
    spread_ok, spread_reason = credit_spread_entry_ready(option, action=action)
    if not spread_ok:
        return SafetyCheck(False, spread_reason, "credit_not_ready")
    return SafetyCheck(True, "ok", "ok")


def acquire_execution_lock(instrument_key: str) -> threading.Lock:
    return _instrument_lock(instrument_key)
