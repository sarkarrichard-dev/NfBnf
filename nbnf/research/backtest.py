from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from nbnf.brain import ml_core
from nbnf.ml.features import build_features
from nbnf.ml.training_set import LabelConfig, describe_training_frame, make_supervised_frame


@dataclass(frozen=True)
class BacktestConfig:
    horizon_bars: int = 5
    long_threshold: float = 0.25
    short_threshold: float = -0.25
    cost_bps: float = 8.0
    min_history_bars: int = 60


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
    if len(df) < cfg.min_history_bars + cfg.horizon_bars + 1:
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
    for i in range(cfg.min_history_bars, len(df) - cfg.horizon_bars):
        hist = df.iloc[: i + 1].copy()
        metrics, tags = build_features(hist)
        signal = ml_core.infer(metrics, tags, hist)
        side = 0
        if signal.score >= cfg.long_threshold:
            side = 1
        elif signal.score <= cfg.short_threshold:
            side = -1
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
                "score": round(float(signal.score), 4),
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
        "warning": "Research only. Results exclude intraday fills, option liquidity, broker limits, and taxes.",
    }
    return {
        "symbol": symbol,
        "config": asdict(cfg),
        "dataset": describe_training_frame(supervised),
        "summary": summary,
        "trades": trades[-50:],
    }
