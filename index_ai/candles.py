from __future__ import annotations

import pandas as pd


def latest_two_sessions(candles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candles.empty:
        raise ValueError("Dhan returned no candle data.")
    frame = candles.copy()
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
    frame["session"] = pd.to_datetime(frame["datetime"]).dt.date
    dates = sorted(frame["session"].unique())
    if len(dates) < 2:
        raise ValueError("Need at least two trading sessions for CPR and EMA.")
    previous_day = frame[frame["session"] == dates[-2]]
    today = frame[frame["session"] == dates[-1]]
    return today.reset_index(drop=True), previous_day.reset_index(drop=True)


def prepare_intraday_signal_frames(
    candles: pd.DataFrame,
    *,
    min_ema_bars: int = 21,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    CPR uses the prior full session; EMA uses the latest session when it has enough bars.

    Early in the day (or after a thin session) the current session may have < ``min_ema_bars``
    rows — then EMA is computed on the last ``min_ema_bars`` candles across sessions while CPR
    still uses the previous session OHLC.
    """
    frame = candles.copy()
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
    if frame.empty:
        raise ValueError("Dhan returned no usable candle rows.")

    today, previous = latest_two_sessions(frame)
    if len(previous) < 1:
        raise ValueError("Need a prior trading session to build CPR levels.")

    if len(today) >= min_ema_bars:
        return today, previous

    rolling = frame.sort_values("datetime").tail(min_ema_bars)
    if len(rolling) < min_ema_bars:
        raise ValueError(
            f"Need at least {min_ema_bars} intraday candles for EMA; only {len(rolling)} available. "
            "Try again after more bars form, or download intraday history first."
        )
    return rolling.reset_index(drop=True), previous
