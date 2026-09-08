"""Crypto strategy engines — pure functions over OHLCV candle frames.

Each ``step(...)`` takes the candles plus a small state dict and returns
``(new_state, event)``. The lane (``crypto.lanes``) owns persistence, sizing,
journaling; the strategy owns only the signal.
"""
