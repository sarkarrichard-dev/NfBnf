from __future__ import annotations

import argparse
import json

from trading_ai_engine.ml.market_learn import (
    InternetDatasetConfig,
    build_market_training_frame,
    download_indian_market_history,
    learning_status,
    train_market_model,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download 6-7 years of Indian market daily data and train Trading AI Workstation AIML."
    )
    parser.add_argument("--years", type=int, default=7, help="History window, usually 6 or 7 years.")
    parser.add_argument("--max-symbols", type=int, default=80, help="Maximum catalog symbols to fetch.")
    parser.add_argument("--download", action="store_true", help="Download Yahoo/yfinance OHLCV CSVs.")
    parser.add_argument("--build-frame", action="store_true", help="Build supervised training CSV.")
    parser.add_argument("--train", action="store_true", help="Train local market model.")
    parser.add_argument("--status", action="store_true", help="Print current dataset/model status.")
    args = parser.parse_args()

    cfg = InternetDatasetConfig(years=args.years, period=f"{args.years}y", max_symbols=args.max_symbols)
    out: dict[str, object] = {}
    if args.download:
        out["download"] = download_indian_market_history(config=cfg)
        out["frame"] = build_market_training_frame(cfg)
    if args.build_frame:
        out["frame"] = build_market_training_frame(cfg)
    if args.train:
        out["train"] = train_market_model()
    if args.status or not out:
        out["status"] = learning_status()
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
