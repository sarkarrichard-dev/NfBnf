from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.candles import latest_two_sessions, prepare_intraday_signal_frames
from index_ai.config import AppSettings
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.executor import build_execution_plan
from index_ai.instruments import configured_index_keys, get_instrument
from index_ai.options_oi import (
    analyze_option_chain,
    apply_oi_to_signal,
    choose_option_from_chain_with_oi,
)
from index_ai.risk_policy import HARDCODED_RISK
from index_ai.option_structures import CREDIT_ACTIONS, build_credit_structure
from index_ai.strategy import StrategySignal, copy_signal
from index_ai.options_expiry import pick_nearest_expiry
from index_ai.strategy_router import route_intraday_signal

INDEX_KEYS = configured_index_keys()


def plan_instrument(
    *,
    client: DhanClient,
    app_settings: AppSettings,
    instrument_key: str,
    lookback_days: int = 5,
    interval: str = "5",
) -> dict[str, Any]:
    """CPR+EMA + Dhan option-chain OI for NIFTY / BANKNIFTY (no order placed)."""
    instrument = get_instrument(instrument_key)
    if instrument.underlying_security_id is None:
        return {
            "instrument": instrument.__dict__,
            "error": f"{instrument.label} security id is not configured in .env.",
        }

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = now - timedelta(days=lookback_days)
    data = client.intraday_history(
        instrument,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
    )
    candles = chart_response_to_frame(data)
    ema_frame, previous = prepare_intraday_signal_frames(candles)
    signal, cpr_regime = route_intraday_signal(
        ema_frame,
        previous,
        allow_option_selling=app_settings.risk.allow_option_selling,
    )

    oi_context: dict[str, Any] | None = None
    option = None
    expiry = None
    if signal.action != "NO_TRADE":
        expiries = client.expiry_list(instrument)
        expiry = pick_nearest_expiry(expiries, now=now) if expiries else None
        if expiry:
            chain = client.option_chain(instrument, expiry)
            if signal.action in CREDIT_ACTIONS:
                try:
                    option = build_credit_structure(chain, signal, instrument, cpr_regime)
                    if option:
                        option = {**option, "expiry": expiry}
                except Exception as exc:
                    signal = copy_signal(
                        signal,
                        action="NO_TRADE",
                        reason=f"Credit structure build failed: {exc}",
                        confidence=0.0,
                    )
            else:
                oi = analyze_option_chain(chain, spot=signal.price, instrument=instrument)
                oi_context = oi.to_dict()
                signal = apply_oi_to_signal(signal, oi)
                if signal.confidence < HARDCODED_RISK.min_confidence:
                    signal = copy_signal(
                        signal,
                        action="NO_TRADE",
                        reason=(
                            f"OI-filtered: confidence {signal.confidence} below gate after OI adjustment."
                        ),
                        confidence=0.0,
                    )
                else:
                    option = choose_option_from_chain_with_oi(
                        chain,
                        signal,
                        instrument,
                        oi,
                        transaction_type=HARDCODED_RISK.default_option_transaction,
                    )
                    if option and expiry:
                        option = {**option, "expiry": expiry}

    plan = build_execution_plan(
        app_settings=app_settings,
        instrument=instrument,
        signal=signal,
        option=option,
    )
    today_session, _ = latest_two_sessions(candles)
    return {
        "instrument": instrument.__dict__,
        "instrument_key": instrument_key,
        "signal": signal.to_dict(),
        "cpr_regime": cpr_regime.to_dict(),
        "oi": oi_context,
        "expiry": expiry,
        "option": option,
        "plan": plan.__dict__,
        "candles_used": {
            "today_session": len(today_session),
            "ema_bars": len(ema_frame),
            "from": str(candles.iloc[0]["datetime"]) if not candles.empty else None,
            "to": str(candles.iloc[-1]["datetime"]) if not candles.empty else None,
        },
    }
