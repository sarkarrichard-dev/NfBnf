"""Live multi-timeframe candlestick context + gating against trained calibration."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from trading_ai_engine.market_yfinance import history
from trading_ai_engine.ml.candlestick_patterns import (
    PATTERN_DIRECTION,
    PATTERN_FEATURE_COLUMNS,
    snapshot_patterns_by_tf,
)
from trading_ai_engine.ml.intraday_tf import resample_ohlc
from trading_ai_engine.trading.derivatives_focus import suggested_period_for_interval


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _pattern_gate_enabled() -> bool:
    return os.environ.get("TRADING_AI_PATTERN_GATE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# Yahoo-supported intraday steps; 2h/3h are resampled from finer bars in ``_resample_ohlc``.
INTRADAY_FETCH_ORDER: tuple[tuple[str, str], ...] = (
    ("5m", "5m"),
    ("15m", "15m"),
    ("30m", "30m"),
    ("60m", "60m"),
    ("90m", "90m"),
)


def fetch_multi_tf_intraday(symbol: str) -> dict[str, pd.DataFrame]:
    """
    Pull 5m–90m from Yahoo where supported, then derive 2h / 3h bars via resampling.
    """
    out: dict[str, pd.DataFrame] = {}
    for label, interval in INTRADAY_FETCH_ORDER:
        period = suggested_period_for_interval(interval)
        try:
            df = history(symbol, period=period, interval=interval, auto_adjust=False)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        out[label] = df
    base = out.get("5m") or out.get("15m")
    if base is not None and not base.empty:
        r2 = resample_ohlc(base, "2h")
        if not r2.empty:
            out["120m"] = r2
        r3 = resample_ohlc(base, "3h")
        if not r3.empty:
            out["180m"] = r3
    return out


def _effective_win_rate(
    cal_iv: dict[str, Any],
    pat: str,
    live_overlay: dict[str, Any] | None,
    iv: str,
) -> float | None:
    cell = (cal_iv or {}).get(pat) or {}
    wr = cell.get("win_rate")
    n_hist = int(cell.get("n") or 0)
    live_bucket = (live_overlay or {}).get(iv) or {}
    live_cell = live_bucket.get(pat) or {}
    w = int(live_cell.get("wins") or 0)
    l_ = int(live_cell.get("losses") or 0)
    n_live = w + l_
    if n_live <= 0:
        return float(wr) if wr is not None else None
    live_wr = w / max(n_live, 1)
    if wr is None or n_hist <= 0:
        return live_wr
    # shrinkage: trust live more when more live samples
    alpha = min(0.65, n_live / (n_live + max(n_hist, 1)))
    return (1.0 - alpha) * float(wr) + alpha * live_wr


def build_candle_pattern_context(symbol: str) -> dict[str, Any]:
    """
    Multi-TF pattern scan + best historical win rates aligned to bull/bear hints.
    Used for AIML gating (default: require >= 65% on at least one firing pattern).
    """
    from trading_ai_engine.ml.market_learn import load_market_model

    min_wr = _env_float("TRADING_AI_PATTERN_MIN_WIN_RATE", 0.65)
    model = load_market_model() or {}
    raw_cal = model.get("pattern_calibration") or {}
    live_overlay = model.get("pattern_live_overlay") or {}

    multi = fetch_multi_tf_intraday(symbol)
    snap = snapshot_patterns_by_tf(multi)

    max_bull: float | None = None
    max_bear: float | None = None
    bull_hits: list[str] = []
    bear_hits: list[str] = []

    calibration_active = bool(raw_cal) and _pattern_gate_enabled()

    for iv, fired in snap.items():
        cal_iv = raw_cal.get(iv) or {}
        for pat, _v in fired.items():
            if pat not in PATTERN_FEATURE_COLUMNS:
                continue
            direction = PATTERN_DIRECTION.get(pat, 0)
            if direction == 0:
                continue
            wr = _effective_win_rate(cal_iv, pat, live_overlay, iv)
            if wr is None:
                continue
            if direction == 1:
                bull_hits.append(f"{iv}:{pat}")
                max_bull = wr if max_bull is None else max(max_bull, wr)
            elif direction == -1:
                bear_hits.append(f"{iv}:{pat}")
                max_bear = wr if max_bear is None else max(max_bear, wr)

    return {
        "calibration_active": calibration_active,
        "min_win_rate": min_wr,
        "multi_tf_bars": {k: int(len(v)) for k, v in multi.items()},
        "pattern_snapshot": snap,
        "max_win_rate_bullish_patterns": max_bull,
        "max_win_rate_bearish_patterns": max_bear,
        "bullish_pattern_hits": bull_hits,
        "bearish_pattern_hits": bear_hits,
    }


def pattern_gate_blocks_plan(metrics: dict[str, Any], brain_action: str) -> bool:
    """
    Returns True when a directional trade should be blocked for lack of a strong
    historically-calibrated pattern (>= min win rate).
    """
    if not _pattern_gate_enabled():
        return False
    ctx = metrics.get("candle_pattern_context") or {}
    if not ctx.get("calibration_active"):
        return False
    min_wr = float(ctx.get("min_win_rate") or 0.65)
    act = (brain_action or "").strip().lower()
    if act == "bullish":
        m = ctx.get("max_win_rate_bullish_patterns")
        if m is None:
            return True
        return float(m) < min_wr
    if act == "bearish":
        m = ctx.get("max_win_rate_bearish_patterns")
        if m is None:
            return True
        return float(m) < min_wr
    return False


def attach_pattern_context_to_metrics(metrics: dict[str, Any], symbol: str) -> None:
    """Mutate ``metrics`` with ``candle_pattern_context`` (call after primary OHLC features exist)."""
    try:
        ctx = build_candle_pattern_context(symbol)
    except Exception as exc:  # noqa: BLE001 — keep analyze resilient to Yahoo failures
        metrics["candle_pattern_context"] = {
            "error": str(exc),
            "calibration_active": False,
        }
        metrics["pattern_snapshot"] = {}
        return
    metrics["candle_pattern_context"] = ctx
    metrics["pattern_snapshot"] = ctx.get("pattern_snapshot") or {}
