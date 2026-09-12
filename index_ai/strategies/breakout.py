"""Break Res / Break Sup — close through prior range (AK Roxx-style)."""

from __future__ import annotations

import pandas as pd


def detect_breakout(candles: pd.DataFrame, *, lookback: int = 20, confirm_bars: int = 1) -> dict:
    """
    Break Res: last close above highest high of prior ``lookback`` bars.
    Break Sup: last close below lowest low of prior ``lookback`` bars.

    ``confirm_bars`` (Richard, 2026-09-12: reject "fake breakouts" on the buy
    lane) requires the last N closes to *all* hold beyond the level, not just
    the latest one — a single-bar poke through the range that snaps straight
    back fails this and is never signalled as a breakout in the first place.
    A live scanner can't detect a fake break by hindsight (the next bar hasn't
    happened yet); requiring it to hold is the live-safe substitute.
    """
    confirm_bars = max(1, int(confirm_bars))
    if len(candles) < lookback + 1 + confirm_bars:
        return {
            "ready": False,
            "break_res": False,
            "break_sup": False,
            "range_high": None,
            "range_low": None,
        }

    prior = candles.iloc[-(lookback + confirm_bars) : -confirm_bars]
    recent = candles.iloc[-confirm_bars:]
    range_high = float(prior["high"].max())
    range_low = float(prior["low"].min())
    before_close = float(prior["close"].iloc[-1])  # bar right before the hold window began

    held_above = bool((recent["close"].astype(float) > range_high).all())
    held_below = bool((recent["close"].astype(float) < range_low).all())
    break_res = held_above and before_close <= range_high
    break_sup = held_below and before_close >= range_low

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
