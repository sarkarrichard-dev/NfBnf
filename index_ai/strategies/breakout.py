"""Break Res / Break Sup — close through prior range (AK Roxx-style)."""

from __future__ import annotations

import pandas as pd


def detect_breakout(candles: pd.DataFrame, *, lookback: int = 20, confirm_bars: int = 1) -> dict:
    """
    Break Res: the last ``confirm_bars`` closes all hold above the highest
    high of the ``lookback`` bars before that window (and the bar just
    before the window hadn't already broken out — so this fires once, right
    when confirmation completes, not on every bar price stays up there).
    Break Sup is the mirror below the range low.

    ``confirm_bars=1`` (the old, single-bar behaviour) is a plain close
    crossing the level — the classic setup for a fakeout, since a level
    getting poked through for one bar proves nothing. 2026-09-16: raised the
    default caller-side to require 2 confirmed closes after a bad day traced
    partly to single-bar breakout entries reversing straight into their stop.
    """
    n = max(1, int(confirm_bars))
    if len(candles) < lookback + n + 1:
        return {
            "ready": False,
            "break_res": False,
            "break_sup": False,
            "range_high": None,
            "range_low": None,
        }

    window = candles.iloc[-(lookback + n) : -n]
    recent_closes = candles["close"].astype(float).iloc[-n:]
    pre_close = float(candles["close"].iloc[-(n + 1)])
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())

    break_res = bool((recent_closes > range_high).all()) and pre_close <= range_high
    break_sup = bool((recent_closes < range_low).all()) and pre_close >= range_low

    tag = ""
    if break_res:
        tag = "BREAK_RES"
    elif break_sup:
        tag = "BREAK_SUP"

    return {
        "ready": True,
        "break_res": break_res,
        "break_sup": break_sup,
        "breakout_tag": tag,
        "range_high": range_high,
        "range_low": range_low,
    }
