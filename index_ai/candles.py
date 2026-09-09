from __future__ import annotations

import pandas as pd

_RULE_MIN = {"1min": 1, "3min": 3, "5min": 5, "15min": 15, "25min": 25, "30min": 30, "60min": 60}


def resample_ohlcv(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Downsample a naive-IST OHLCV frame to ``rule`` (e.g. ``"5min"``, ``"15min"``),
    grouped by session date so a bar never straddles the overnight gap.

    Returns a fresh frame with the same columns; ``datetime`` is the bar-open
    timestamp. The still-forming final bin is dropped — a live feed is polled to
    ``now``, so its last group holds only a fraction of a bar and would flicker
    the EMA / breakout / volume reads that depend on a *closed* bar. If the source
    is already at or coarser than ``rule`` it is returned unchanged.
    """
    if frame is None or getattr(frame, "empty", True):
        return frame
    df = frame.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    target = _RULE_MIN.get(rule, 5)
    src_min = 1.0
    if len(df) >= 2:
        gaps = df["datetime"].diff().dt.total_seconds().dropna()
        src_min = (gaps.mode().iloc[0] if len(gaps.mode()) else gaps.median()) / 60.0
        if src_min >= target:
            return df
    # a bin [t, t+rule) is closed once the source covers up to t+rule, i.e. the
    # last source bar opens at or after t+rule-src_interval
    covered_to = df["datetime"].iloc[-1] + pd.Timedelta(minutes=src_min)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    parts = [
        g.set_index("datetime")
        .resample(rule, origin="start_day", label="left", closed="left")
        .agg(agg)
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
        for _, g in df.groupby(df["datetime"].dt.date, sort=True)
    ]
    out = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
    if len(out) and out["datetime"].iloc[-1] + pd.Timedelta(rule) > covered_to:
        out = out.iloc[:-1]
    return out[[c for c in df.columns if c in out.columns]]


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


if __name__ == "__main__":  # ponytail self-check
    _n = 30
    _one = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=_n, freq="1min"),
            "open": range(_n),
            "high": [x + 2 for x in range(_n)],
            "low": [x - 2 for x in range(_n)],
            "close": [x + 1 for x in range(_n)],
            "volume": [100.0] * _n,
        }
    )
    _five = resample_ohlcv(_one, "5min")
    assert len(_five) == 6, len(_five)
    assert _five.iloc[0]["open"] == 0 and _five.iloc[0]["high"] == 6 and _five.iloc[0]["close"] == 5
    assert _five.iloc[0]["volume"] == 500.0
    # already coarse -> passthrough (same object rows)
    assert len(resample_ohlcv(_five, "5min")) == 6
    assert len(resample_ohlcv(_one, "15min")) == 2
    # a still-forming final 5m bin is dropped: 33 1m bars end mid-bin
    _n2 = 33
    _one2 = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-02 09:15", periods=_n2, freq="1min"),
            "open": range(_n2),
            "high": [x + 2 for x in range(_n2)],
            "low": [x - 2 for x in range(_n2)],
            "close": [x + 1 for x in range(_n2)],
            "volume": [100.0] * _n2,
        }
    )
    assert len(resample_ohlcv(_one2, "5min")) == 6, len(resample_ohlcv(_one2, "5min"))
    print("candles.py self-check ok")
