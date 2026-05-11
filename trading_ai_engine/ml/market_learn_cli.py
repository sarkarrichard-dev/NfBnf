from __future__ import annotations

import argparse
import json

from trading_ai_engine.ml.market_learn import (
    InternetDatasetConfig,
    build_market_training_frame,
    download_indian_intraday_5m,
    download_indian_market_history,
    learning_status,
    train_market_model,
)
from trading_ai_engine.ml.pattern_feedback import refresh_pattern_live_overlay


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download 6-7 years of Indian market daily data and train Trading AI Workstation AIML."
    )
    parser.add_argument("--years", type=int, default=7, help="History window, usually 6 or 7 years.")
    parser.add_argument("--max-symbols", type=int, default=80, help="Maximum catalog symbols to fetch.")
    parser.add_argument("--download", action="store_true", help="Download Yahoo/yfinance OHLCV CSVs.")
    parser.add_argument(
        "--intraday-download",
        action="store_true",
        help="Download 5m Yahoo bars (60d default) and expand 5m–3h for candlestick training rows.",
    )
    parser.add_argument(
        "--intraday-period",
        type=str,
        default="60d",
        help="Yahoo period for intraday 5m pull (e.g. 30d, 60d).",
    )
    parser.add_argument("--build-frame", action="store_true", help="Build supervised training CSV.")
    parser.add_argument("--train", action="store_true", help="Train local market model.")
    parser.add_argument(
        "--pattern-feedback",
        action="store_true",
        help="Refresh pattern_live_overlay from closed paper trades in SQLite.",
    )
    parser.add_argument("--status", action="store_true", help="Print current dataset/model status.")
    args = parser.parse_args()

    cfg = InternetDatasetConfig(
        years=args.years,
        period=f"{args.years}y",
        max_symbols=args.max_symbols,
        intraday_symbols_cap=min(args.max_symbols, 40),
        intraday_period=args.intraday_period,
    )
    out: dict[str, object] = {}
    if args.download:
        out["download"] = download_indian_market_history(config=cfg)
        out["frame"] = build_market_training_frame(cfg)
    if args.intraday_download:
        out["intraday_download"] = download_indian_intraday_5m(config=cfg)
        out["frame"] = build_market_training_frame(cfg)
    if args.build_frame:
        out["frame"] = build_market_training_frame(cfg)
    if args.train:
        out["train"] = train_market_model()
    if args.pattern_feedback:
        out["pattern_feedback"] = refresh_pattern_live_overlay()
    if args.status or not out:
        out["status"] = learning_status()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
