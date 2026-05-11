"""
Central Pivot Range (CPR) from the **prior** session day and EMA stack features.

CPR (common Indian desk definition for the current session):
  P  = (H + L + C) / 3  of the **previous** completed session day
  BC = (H + L) / 2
  TC = 2 * P - BC

``_session_day`` is ``normalize(date)`` so intraday bars share one CPR set per calendar day.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

CPR_EMA_FEATURE_COLUMNS: list[str] = [
    "cpr_pct_from_p",
    "cpr_pos_in_band",
    "ema9_gap",
    "ema21_gap",
    "ema9_vs_ema21_gap",
]


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.astype(float).ewm(span=span, adjust=False).mean()


def add_cpr_ema_columns(df: pd.DataFrame, *, drop_intermediate: bool = True) -> pd.DataFrame:
    """Append CPR + EMA numeric columns (CPR uses **previous** completed session day, no lookahead)."""
    out = df.copy()
    if "close" not in out.columns or out.empty:
        for c in CPR_EMA_FEATURE_COLUMNS:
            out[c] = 0.0
        return out

    for col in ("high", "low", "open"):
        if col not in out.columns:
            out[col] = out["close"]

    ts = pd.to_datetime(out["date"], errors="coerce")
    out["_session_day"] = ts.dt.normalize()

    daily = (
        out.groupby("_session_day", sort=True)
        .agg(day_h=("high", "max"), day_l=("low", "min"), day_c=("close", "last"))
        .reset_index()
    )
    daily["cpr_p"] = (daily["day_h"] + daily["day_l"] + daily["day_c"]) / 3.0
    daily["cpr_bc"] = (daily["day_h"] + daily["day_l"]) / 2.0
    daily["cpr_tc"] = 2.0 * daily["cpr_p"] - daily["cpr_bc"]
    daily["cpr_p_prev"] = daily["cpr_p"].shift(1)
    daily["cpr_bc_prev"] = daily["cpr_bc"].shift(1)
    daily["cpr_tc_prev"] = daily["cpr_tc"].shift(1)

    out = out.merge(
        daily[["_session_day", "cpr_p_prev", "cpr_bc_prev", "cpr_tc_prev"]],
        on="_session_day",
        how="left",
    )
    c = out["close"].astype(float)
    p = out["cpr_p_prev"].astype(float)
    bc = out["cpr_bc_prev"].astype(float)
    tc = out["cpr_tc_prev"].astype(float)
    width = (tc - bc).abs().replace(0, pd.NA)
    out["cpr_pct_from_p"] = ((c - p) / c.replace(0, pd.NA)).fillna(0.0)
    pos = (c - bc) / width
    out["cpr_pos_in_band"] = pos.clip(-3.0, 4.0).fillna(0.0)

    ema9 = _ema(c, 9)
    ema21 = _ema(c, 21)
    out["ema9_gap"] = (c / ema9.replace(0, pd.NA) - 1.0).fillna(0.0)
    out["ema21_gap"] = (c / ema21.replace(0, pd.NA) - 1.0).fillna(0.0)
    out["ema9_vs_ema21_gap"] = (ema9 / ema21.replace(0, pd.NA) - 1.0).fillna(0.0)

    if drop_intermediate:
        out.drop(
            columns=["_session_day", "cpr_p_prev", "cpr_bc_prev", "cpr_tc_prev"],
            inplace=True,
            errors="ignore",
        )
    for col in CPR_EMA_FEATURE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out


def cpr_structure_bias(close: float, p: float, bc: float, tc: float) -> float:
    """Rough -1..+1 bias from price vs CPR band."""
    if not all(pd.notna([close, p, bc, tc])):
        return 0.0
    width = max(abs(tc - bc), max(abs(float(close)), 1.0) * 1e-6)
    if close > tc:
        return float(max(-1.0, min(1.0, (close - tc) / width)))
    if close < bc:
        return float(-max(-1.0, min(1.0, (bc - close) / width)))
    if close > p:
        return float(0.35 * max(-1.0, min(1.0, (close - p) / width)))
    if close < p:
        return float(-0.35 * max(-1.0, min(1.0, (p - close) / width)))
    mid = 0.5 * (tc + bc)
    return float(0.15 * max(-1.0, min(1.0, (close - mid) / width)))


def ema_stack_bias(ema9: float, ema21: float, close: float) -> float:
    """EMA9 vs EMA21 and price vs EMA9, mapped to ~-1..+1."""
    if not all(pd.notna([ema9, ema21, close])):
        return 0.0
    if ema21 == 0:
        return 0.0
    cross = (ema9 - ema21) / max(abs(ema21), 1e-9)
    dist = (close - ema9) / max(abs(ema9), 1e-9)
    raw = 0.65 * math.tanh(cross * 25.0) + 0.35 * math.tanh(dist * 15.0)
    return float(max(-1.0, min(1.0, raw)))


def last_row_cpr_ema_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Last-bar CPR levels (prior day), EMAs, numeric features, and structural biases."""
    if df is None or df.empty:
        return {}
    d = add_cpr_ema_columns(df, drop_intermediate=False)
    last = d.iloc[-1]
    c = float(last["close"])
    p = float(last["cpr_p_prev"]) if pd.notna(last.get("cpr_p_prev")) else float("nan")
    bc = float(last["cpr_bc_prev"]) if pd.notna(last.get("cpr_bc_prev")) else float("nan")
    tc = float(last["cpr_tc_prev"]) if pd.notna(last.get("cpr_tc_prev")) else float("nan")

    ema9_s = _ema(d["close"].astype(float), 9)
    ema21_s = _ema(d["close"].astype(float), 21)
    ema9 = float(ema9_s.iloc[-1])
    ema21 = float(ema21_s.iloc[-1])

    out: dict[str, Any] = {
        "cpr_p": float(p) if pd.notna(p) else None,
        "cpr_bc": float(bc) if pd.notna(bc) else None,
        "cpr_tc": float(tc) if pd.notna(tc) else None,
        "ema9": float(ema9) if pd.notna(ema9) else None,
        "ema21": float(ema21) if pd.notna(ema21) else None,
        "cpr_pct_from_p": float(last.get("cpr_pct_from_p") or 0.0),
        "cpr_pos_in_band": float(last.get("cpr_pos_in_band") or 0.0),
        "ema9_gap": float(last.get("ema9_gap") or 0.0),
        "ema21_gap": float(last.get("ema21_gap") or 0.0),
        "ema9_vs_ema21_gap": float(last.get("ema9_vs_ema21_gap") or 0.0),
    }
    if pd.notna(p) and pd.notna(bc) and pd.notna(tc):
        out["cpr_structure_bias"] = cpr_structure_bias(c, p, bc, tc)
    else:
        out["cpr_structure_bias"] = 0.0
    out["ema_stack_bias"] = ema_stack_bias(ema9, ema21, c)
    return out
