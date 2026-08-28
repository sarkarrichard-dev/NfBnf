from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from index_ai.backtest import _sessions, replay_session
from index_ai.config import AppSettings, DhanSettings, RiskSettings
from index_ai.instruments import get_instrument


def _candles(days: int = 2, bars_per_day: int = 30) -> pd.DataFrame:
    ist = ZoneInfo("Asia/Kolkata")
    rows = []
    base = datetime(2026, 6, 2, 9, 15, tzinfo=ist)
    price = 100.0
    for d in range(days):
        for b in range(bars_per_day):
            ts = base + timedelta(days=d, minutes=5 * b)
            price += 0.5
            rows.append(
                {
                    "datetime": ts,
                    "open": price - 0.2,
                    "high": price + 0.3,
                    "low": price - 0.3,
                    "close": price,
                    "volume": 1000,
                }
            )
    return pd.DataFrame(rows)


def _settings() -> AppSettings:
    return AppSettings(
        dhan=DhanSettings(
            client_id="1",
            access_token="x",
            api_base_url="https://api.dhan.co/v2",
            api_key="k",
            api_secret="s",
            auth_base_url="https://auth.dhan.co",
            token_expiry="",
        ),
        risk=RiskSettings(
            trading_mode="PAPER",
            allow_live_trading=False,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000,
            trailing_stop_index_points=25,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )


def test_sessions_split() -> None:
    frame = _candles(2, 10)
    sessions = _sessions(frame)
    assert len(sessions) == 2


def test_replay_session_returns_list() -> None:
    frame = _candles(2, 35)
    sessions = _sessions(frame)
    _, prev = sessions[0]
    _, today = sessions[1]
    combined = pd.concat([prev, today], ignore_index=True)
    from index_ai.candles import prepare_intraday_signal_frames
    from index_ai.strategies.strategy_params import get_strategy_params

    sp = get_strategy_params()
    ema_frame, previous = prepare_intraday_signal_frames(
        combined, min_ema_bars=sp.ema_slow_period + 2
    )
    day = pd.to_datetime(today.iloc[0]["datetime"]).date()
    today_only = ema_frame[pd.to_datetime(ema_frame["datetime"]).dt.date == day].reset_index(drop=True)
    trades = replay_session(
        today_only,
        previous,
        instrument=get_instrument("NIFTY"),
        allow_option_selling=True,
    )
    assert isinstance(trades, list)
