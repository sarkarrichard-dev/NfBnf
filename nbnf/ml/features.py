from __future__ import annotations

import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def build_features(ohlc: pd.DataFrame) -> tuple[dict, list[str]]:
    """
    Return (last_row_metrics dict, active signal tags) from normalized OHLCV.
    Tags drive the feedback loop: each feedback update nudges per-tag EMAs.
    """
    df = ohlc.dropna(subset=["close"]).copy()
    if df.empty:
        return {}, []

    c = df["close"].astype(float)
    df["ret_1d"] = c.pct_change()
    df["sma20"] = c.rolling(20, min_periods=5).mean()
    df["sma50"] = c.rolling(50, min_periods=10).mean()
    df["rsi14"] = _rsi(c, 14)
    vol = df["volume"].astype(float)
    vm = vol.rolling(20, min_periods=5).mean()
    vs = vol.rolling(20, min_periods=5).std()
    df["vol_z"] = (vol - vm) / vs.replace(0, pd.NA)

    last = df.iloc[-1]
    tags: list[str] = []
    sma20, sma50 = last.get("sma20"), last.get("sma50")
    if pd.notna(sma20) and pd.notna(sma50):
        if sma20 > sma50:
            tags.append("uptrend_ma")
        elif sma20 < sma50:
            tags.append("downtrend_ma")

    rsi = last.get("rsi14")
    if pd.notna(rsi):
        if rsi < 30:
            tags.append("rsi_oversold")
        elif rsi > 70:
            tags.append("rsi_overbought")

    vz = last.get("vol_z")
    if pd.notna(vz) and vz > 2:
        tags.append("volume_spike")

    metrics = {
        "last_date": str(last["date"]) if "date" in last.index else None,
        "close": float(last["close"]) if pd.notna(last["close"]) else None,
        "ret_1d": float(last["ret_1d"]) if pd.notna(last.get("ret_1d")) else None,
        "sma20": float(sma20) if pd.notna(sma20) else None,
        "sma50": float(sma50) if pd.notna(sma50) else None,
        "rsi14": float(rsi) if pd.notna(rsi) else None,
        "vol_z": float(vz) if pd.notna(vz) else None,
        "tags": tags,
    }
    return metrics, tags
