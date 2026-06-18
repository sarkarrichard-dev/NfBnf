from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from index_ai.candles import latest_two_sessions, prepare_intraday_signal_frames
from index_ai.config import AppSettings, candle_interval_minutes
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.dhan_errors import classify_http_error
from index_ai.instruments import configured_index_keys, get_instrument
from index_ai.options_oi import analyze_option_chain
from index_ai.options_expiry import pick_nearest_expiry
from index_ai.plan_builder import build_opportunity
from index_ai.strategy_router import evaluate_dual_opportunities

INDEX_KEYS = configured_index_keys()


def plan_instrument(
    *,
    client: DhanClient,
    app_settings: AppSettings,
    instrument_key: str,
    lookback_days: int = 5,
    interval: str | None = None,
) -> dict[str, Any]:
    """Dual-lane plan: candlestick buy + CPR sell (each with option chain + gates)."""
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
    dual = evaluate_dual_opportunities(
        ema_frame,
        previous,
        allow_option_selling=app_settings.risk.allow_option_selling,
        allow_option_buying=app_settings.risk.allow_option_buying,
    )
    signal = dual.primary
    cpr_regime = dual.regime

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

    buy_opp = build_opportunity(
        app_settings=app_settings,
        instrument=instrument,
        signal=dual.buy,
        chain=chain,
        oi=oi,
        expiry=expiry,
        cpr_regime=cpr_regime,
        lane="buy",
    )
    sell_opp = build_opportunity(
        app_settings=app_settings,
        instrument=instrument,
        signal=dual.sell,
        chain=chain,
        oi=oi,
        expiry=expiry,
        cpr_regime=cpr_regime,
        lane="sell",
    )

    opportunities: list[dict[str, Any]] = []
    for opp in (buy_opp, sell_opp):
        if opp and opp.get("plan", {}).get("allowed"):
            opportunities.append(opp)

    option = None
    capital = None
    plan: dict[str, Any] = {
        "allowed": False,
        "reason": "No option selected.",
        "mode": app_settings.risk.trading_mode,
    }
    for opp in (buy_opp, sell_opp):
        if not opp:
            continue
        if opp.get("signal", {}).get("action") == signal.action:
            plan = opp.get("plan") or plan
            option = opp.get("option")
            capital = opp.get("capital_required")
            break

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
        "buy_signal": dual.buy.to_dict(),
        "sell_signal": dual.sell.to_dict(),
        "buy_opportunity": buy_opp,
        "sell_opportunity": sell_opp,
        "opportunities": opportunities,
        "cpr_regime": cpr_regime.to_dict(),
        "oi": oi_context,
        "oi_fetch_error": oi_fetch_error,
        "expiry": expiry,
        "option": option,
        "capital_required": capital,
        "plan": plan if isinstance(plan, dict) else {},
        "candles_used": {
            "today_session": len(today_session),
            "ema_bars": len(ema_frame),
            "interval_minutes": iv,
            "from": str(candles.iloc[0]["datetime"]) if not candles.empty else None,
            "to": str(candles.iloc[-1]["datetime"]) if not candles.empty else None,
        },
        "spot_session": spot_session,
    }
