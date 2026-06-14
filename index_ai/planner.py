from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.candles import latest_two_sessions, prepare_intraday_signal_frames
from index_ai.config import AppSettings, candle_interval_minutes
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.dhan_errors import classify_http_error
from index_ai.executor import build_execution_plan
from index_ai.instruments import configured_index_keys, get_instrument
from index_ai.options_oi import (
    analyze_option_chain,
    apply_oi_to_signal,
    choose_option_from_chain_with_oi,
)
from index_ai.risk_policy import HARDCODED_RISK
from index_ai.credit_spread import CREDIT_ACTIONS
from index_ai.option_structures import build_atm_short_option, build_credit_structure
from index_ai.premium_sell import PREMIUM_SELL_ACTIONS
from index_ai.strategy import StrategySignal, copy_signal
from index_ai.options_expiry import pick_nearest_expiry
from index_ai.strategy_router import route_intraday_signal
from index_ai.trade_lots import stamp_option_quantities
from index_ai.capital_required import compute_capital_required

INDEX_KEYS = configured_index_keys()


def plan_instrument(
    *,
    client: DhanClient,
    app_settings: AppSettings,
    instrument_key: str,
    lookback_days: int = 5,
    interval: str | None = None,
) -> dict[str, Any]:
    """CPR+EMA + Dhan option-chain OI for NIFTY / BANKNIFTY (no order placed)."""
    instrument = get_instrument(instrument_key)
    if instrument.underlying_security_id is None:
        return {
            "instrument": instrument.__dict__,
            "error": f"{instrument.label} security id is not configured in .env.",
        }

    iv = str(interval or candle_interval_minutes())
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = now - timedelta(days=lookback_days)
    data = client.intraday_history(
        instrument,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=iv,
    )
    candles = chart_response_to_frame(data)
    from index_ai.strategy_params import get_strategy_params

    sp = get_strategy_params()
    ema_frame, previous = prepare_intraday_signal_frames(
        candles,
        min_ema_bars=sp.ema_slow_period + 2,
    )
    signal, cpr_regime = route_intraday_signal(
        ema_frame,
        previous,
        allow_option_selling=app_settings.risk.allow_option_selling,
        allow_option_buying=app_settings.risk.allow_option_buying,
    )

    oi_context: dict[str, Any] | None = None
    oi_fetch_error: str | None = None
    option = None
    expiry = None
    chain = None
    oi = None
    try:
        expiries = client.expiry_list(instrument)
        expiry = pick_nearest_expiry(expiries, now=now) if expiries else None
        if not expiries:
            oi_fetch_error = "Dhan returned no expiries for this index."
        elif not expiry:
            oi_fetch_error = "Could not parse a tradable expiry from Dhan."
        else:
            chain = client.option_chain(instrument, expiry)
            if not chain:
                oi_fetch_error = "Dhan option chain response was empty."
            else:
                oi = analyze_option_chain(chain, spot=signal.price, instrument=instrument)
                oi_context = oi.to_dict()
    except Exception as exc:
        oi_fetch_error = str(classify_http_error(exc, f"{instrument_key} option chain"))

    if signal.action != "NO_TRADE" and chain and oi:
        from index_ai.strategy_params import get_strategy_params

        sp = get_strategy_params()
        if signal.action in PREMIUM_SELL_ACTIONS:
            try:
                work_signal = signal
                if sp.apex_use_hedged_spreads:
                    from index_ai.apex_pivot_trend import map_apex_to_hedged_credit

                    hedged = map_apex_to_hedged_credit(signal.action)
                    if hedged:
                        work_signal = copy_signal(
                            signal,
                            action=hedged,
                            reason=f"{signal.reason} (hedged spread for defined risk).",
                        )
                        option = build_credit_structure(chain, work_signal, instrument, cpr_regime)
                    else:
                        option = None
                else:
                    option = build_atm_short_option(chain, signal, instrument)
                if option:
                    option = {
                        **option,
                        "expiry": expiry,
                        "chain_pcr": oi.pcr,
                        "chain_bias": oi.bias,
                        "oi_note": oi.note,
                    }
            except Exception as exc:
                signal = copy_signal(
                    signal,
                    action="NO_TRADE",
                    reason=f"Apex option build failed: {exc}",
                    confidence=0.0,
                )
        elif signal.action in CREDIT_ACTIONS:
            try:
                option = build_credit_structure(chain, signal, instrument, cpr_regime)
                if option:
                    option = {
                        **option,
                        "expiry": expiry,
                        "chain_pcr": oi.pcr,
                        "chain_bias": oi.bias,
                        "oi_note": oi.note,
                    }
            except Exception as exc:
                signal = copy_signal(
                    signal,
                    action="NO_TRADE",
                    reason=f"Credit structure build failed: {exc}",
                    confidence=0.0,
                )
        else:
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

    if option:
        option = stamp_option_quantities(option, instrument)

    capital = compute_capital_required(option, instrument) if option else None

    plan = build_execution_plan(
        app_settings=app_settings,
        instrument=instrument,
        signal=signal,
        option=option,
    )
    today_session, _ = latest_two_sessions(candles)
    from index_ai.session_snapshot import spot_session_metrics

    spot_session = spot_session_metrics(
        today_session,
        signal=signal.to_dict(),
        regime=cpr_regime.to_dict(),
    )
    return {
        "instrument": instrument.__dict__,
        "instrument_key": instrument_key,
        "signal": signal.to_dict(),
        "cpr_regime": cpr_regime.to_dict(),
        "oi": oi_context,
        "oi_fetch_error": oi_fetch_error,
        "expiry": expiry,
        "option": option,
        "capital_required": capital,
        "plan": plan.__dict__,
        "candles_used": {
            "today_session": len(today_session),
            "ema_bars": len(ema_frame),
            "interval_minutes": iv,
            "from": str(candles.iloc[0]["datetime"]) if not candles.empty else None,
            "to": str(candles.iloc[-1]["datetime"]) if not candles.empty else None,
        },
        "spot_session": spot_session,
    }
