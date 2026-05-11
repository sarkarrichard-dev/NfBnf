from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Literal

import pandas as pd

from trading_ai_engine.brain import ml_core
from trading_ai_engine.ml.features import build_features
from trading_ai_engine.ml.training_set import LabelConfig, describe_training_frame, make_supervised_frame

SignalMode = Literal["structural", "trend_ma", "mean_reversion_z"]


@dataclass(frozen=True)
class BacktestConfig:
    """Walk-forward research config. ``signal_mode`` selects how each bar's side is chosen."""

    horizon_bars: int = 5
    long_threshold: float = 0.25
    short_threshold: float = -0.25
    cost_bps: float = 8.0
    min_history_bars: int = 60
    signal_mode: SignalMode = "structural"
    fast_ma: int = 20
    slow_ma: int = 50
    z_lookback: int = 20
    z_entry: float = 1.0


def _effective_min_bars(cfg: BacktestConfig) -> int:
    m = cfg.min_history_bars
    if cfg.signal_mode == "trend_ma":
        m = max(m, cfg.slow_ma + 2)
    elif cfg.signal_mode == "mean_reversion_z":
        m = max(m, cfg.z_lookback + 2)
    return m


def _signal_trend_ma(closes: pd.Series, fast: int, slow: int) -> tuple[int, float]:
    if fast >= slow or fast < 2:
        return 0, 0.0
    f = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    s = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    if pd.isna(f) or pd.isna(s):
        return 0, 0.0
    fv, sv = float(f), float(s)
    eps = max(abs(sv) * 1e-6, 1e-9)
    if fv > sv + eps:
        return 1, (fv - sv) / max(abs(sv), eps)
    if fv < sv - eps:
        return -1, (fv - sv) / max(abs(sv), eps)
    return 0, (fv - sv) / max(abs(sv), eps)


def _signal_mean_reversion_z(closes: pd.Series, lookback: int, z_entry: float) -> tuple[int, float]:
    if lookback < 3 or len(closes) < lookback + 1:
        return 0, 0.0
    past = closes.iloc[-(lookback + 1) : -1]
    cur = float(closes.iloc[-1])
    st = float(past.std(ddof=0)) or 1e-12
    m = float(past.mean())
    z = (cur - m) / st
    if z < -z_entry:
        return 1, z
    if z > z_entry:
        return -1, z
    return 0, z


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 1.0
    worst = 0.0
    for x in equity:
        peak = max(peak, x)
        if peak:
            worst = min(worst, x / peak - 1.0)
    return worst


def run_research_backtest(
    symbol: str,
    ohlc: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> dict[str, Any]:
    """
    Research-only signal backtest.

    This does not place orders. It walks forward through daily OHLCV, computes the existing
    structural score using only past data, and simulates a fixed-horizon long/short/flat result.
    """
    cfg = config or BacktestConfig()
    df = ohlc.dropna(subset=["close"]).sort_values("date").reset_index(drop=True).copy()
    supervised = make_supervised_frame(
        df,
        LabelConfig(horizon_bars=cfg.horizon_bars),
    )
    min_start = _effective_min_bars(cfg)
    if len(df) < min_start + cfg.horizon_bars + 1:
        return {
            "symbol": symbol,
            "config": asdict(cfg),
            "dataset": describe_training_frame(supervised),
            "trades": [],
            "summary": {
                "status": "not_enough_data",
                "message": "Need more OHLCV bars for walk-forward testing.",
            },
        }

    trades: list[dict[str, Any]] = []
    cost = cfg.cost_bps / 10_000.0
    for i in range(min_start, len(df) - cfg.horizon_bars):
        hist = df.iloc[: i + 1].copy()
        closes = hist["close"]
        tags: dict[str, Any] = {}
        side = 0
        score = 0.0

        if cfg.signal_mode == "structural":
            metrics, tags = build_features(hist)
            signal = ml_core.infer(metrics, tags, hist)
            score = float(signal.score)
            if score >= cfg.long_threshold:
                side = 1
            elif score <= cfg.short_threshold:
                side = -1
        elif cfg.signal_mode == "trend_ma":
            side, score = _signal_trend_ma(closes, cfg.fast_ma, cfg.slow_ma)
            tags = {"signal_mode": "trend_ma", "fast_ma": cfg.fast_ma, "slow_ma": cfg.slow_ma}
        else:
            side, score = _signal_mean_reversion_z(closes, cfg.z_lookback, cfg.z_entry)
            tags = {
                "signal_mode": "mean_reversion_z",
                "z_lookback": cfg.z_lookback,
                "z_entry": cfg.z_entry,
            }

        if side == 0:
            continue

        entry = float(df.loc[i, "close"])
        exit_ = float(df.loc[i + cfg.horizon_bars, "close"])
        gross = (exit_ / entry - 1.0) * side
        net = gross - cost
        trades.append(
            {
                "date": str(df.loc[i, "date"]),
                "side": "long" if side > 0 else "short",
                "score": round(float(score), 4),
                "entry": round(entry, 4),
                "exit": round(exit_, 4),
                "gross_return": round(gross, 6),
                "net_return": round(net, 6),
                "tags": tags,
            }
        )

    equity = [1.0]
    wins = 0
    for t in trades:
        r = float(t["net_return"])
        wins += int(r > 0)
        equity.append(equity[-1] * (1.0 + r))

    returns = pd.Series([float(t["net_return"]) for t in trades], dtype="float64")
    avg = float(returns.mean()) if not returns.empty else 0.0
    std = float(returns.std(ddof=0)) if len(returns) > 1 else 0.0
    summary = {
        "status": "ok",
        "bars": int(len(df)),
        "trades": int(len(trades)),
        "win_rate": round(wins / len(trades), 4) if trades else 0.0,
        "avg_return_per_trade": round(avg, 6),
        "profit_factor": round(
            float(returns[returns > 0].sum() / abs(returns[returns < 0].sum())),
            4,
        )
        if not returns.empty and abs(float(returns[returns < 0].sum())) > 0
        else None,
        "ending_equity": round(equity[-1], 4),
        "max_drawdown": round(_max_drawdown(equity), 4),
        "return_std": round(std, 6),
        "warning": (
            "Research only. Results exclude intraday fills, option liquidity, broker limits, and taxes. "
            "Proxy modes (trend_ma, mean_reversion_z) are simplified teaching baselines, not venue-specific strategies."
        ),
    }
    return {
        "symbol": symbol,
        "config": asdict(cfg),
        "dataset": describe_training_frame(supervised),
        "summary": summary,
        "trades": trades[-50:],
    }
