"""Break Res / Break Sup — close through prior range (AK Roxx-style)."""

from __future__ import annotations

import pandas as pd


def detect_breakout(candles: pd.DataFrame, *, lookback: int = 20) -> dict:
    """
    Break Res: last close above highest high of prior ``lookback`` bars.
    Break Sup: last close below lowest low of prior ``lookback`` bars.
    """
    if len(candles) < lookback + 2:
        return {
            "ready": False,
            "break_res": False,
            "break_sup": False,
            "range_high": None,
            "range_low": None,
        }

    prior = candles.iloc[-(lookback + 1) : -1]
    last = candles.iloc[-1]
    prev = candles.iloc[-2]
    range_high = float(prior["high"].max())
    range_low = float(prior["low"].min())
    close = float(last["close"])
    prev_close = float(prev["close"])

    break_res = close > range_high and prev_close <= range_high
    break_sup = close < range_low and prev_close >= range_low

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
