from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from index_ai.strategy import intraday_strategy_signal


def run_backtest(csv_path: Path) -> dict[str, Any]:
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime")
    df["date"] = df["datetime"].dt.date
    dates = sorted(df["date"].unique())
    trades: list[dict[str, Any]] = []
    for prev_date, day in zip(dates, dates[1:], strict=False):
        prev = df[df["date"] == prev_date]
        today = df[df["date"] == day]
        if len(prev) < 5 or len(today) < 30:
            continue
        for i in range(21, len(today) - 5):
            window = today.iloc[: i + 1]
            signal = intraday_strategy_signal(window, prev)
            if signal.action == "NO_TRADE":
                continue
            entry = float(today.iloc[i]["close"])
            exit_price = float(today.iloc[min(i + 5, len(today) - 1)]["close"])
            direction = 1 if signal.action == "BUY_CALL" else -1
            pnl_points = (exit_price - entry) * direction
            trades.append(
                {
                    "datetime": str(today.iloc[i]["datetime"]),
                    "action": signal.action,
                    "entry": entry,
                    "exit": exit_price,
                    "pnl_points": round(pnl_points, 2),
                    "confidence": signal.confidence,
                }
            )
            break
    wins = sum(1 for t in trades if float(t["pnl_points"]) > 0)
    return {
        "file": str(csv_path),
        "trades": trades,
        "summary": {
            "trades": len(trades),
            "wins": wins,
            "win_rate": round(wins / len(trades), 3) if trades else 0.0,
            "total_points": round(sum(float(t["pnl_points"]) for t in trades), 2),
        },
    }
