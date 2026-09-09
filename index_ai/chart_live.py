"""Live intraday chart helpers (Supertrend refresh for open trades)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from index_ai.candles import prepare_intraday_signal_frames
from index_ai.dhan import DhanClient, chart_response_to_frame
from index_ai.instruments import get_instrument
from index_ai.strategies.strategy_params import get_strategy_params
from index_ai.config import candle_interval_minutes
from index_ai.strategies.supertrend import supertrend_snapshot


def fetch_supertrend_snapshot(client: DhanClient, instrument_key: str) -> dict:
    instrument = get_instrument(instrument_key)
    if instrument.underlying_security_id is None:
        return {"ready": False}
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = now - timedelta(days=5)
    data = client.intraday_history(
        instrument,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=candle_interval_minutes(),
    )
    candles = chart_response_to_frame(data)
    ema_frame, _ = prepare_intraday_signal_frames(candles)
    params = get_strategy_params()
    return supertrend_snapshot(
        ema_frame, period=params.supertrend_period, multiplier=params.supertrend_multiplier
    )
