"""
Backfill the local candle cache with a long history for honest backtesting.

Dhan only serves ~5 calendar days of intraday history per request, so this walks
backwards in 5-day windows and upserts each into memory/candles/<KEY>_<iv>m/.
Needs a valid Dhan token (run the dashboard login first).

    python -m scripts.pull_history --days 400
    python -m scripts.pull_history --days 180 --instruments NIFTY BANKNIFTY --iv 1

Rerun freely — existing days are overwritten, gaps are filled.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 85  # Dhan v2 /charts/intraday allows up to 90 days per request


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="calendar days back to pull")
    ap.add_argument("--iv", default="1", help="candle interval minutes")
    ap.add_argument("--instruments", nargs="*", default=None, help="default: all configured")
    ap.add_argument("--sleep", type=float, default=0.6, help="seconds between requests")
    args = ap.parse_args()

    from index_ai.candle_cache import ingest_frame, list_cached_days
    from index_ai.config import settings
    from index_ai.dhan import DhanClient, chart_response_to_frame
    from index_ai.instruments import configured_index_keys, get_instrument

    cfg = settings()
    if not cfg.dhan.ready:
        raise SystemExit("Dhan token not ready — log in via the dashboard first.")
    client = DhanClient(cfg.dhan)

    keys = [k.upper() for k in (args.instruments or configured_index_keys())]
    now = datetime.now(IST)

    for key in keys:
        inst = get_instrument(key)
        if inst.underlying_security_id is None:
            print(f"{key}: no security id configured — skipping")
            continue
        pulled_days = 0
        end = now
        start_limit = now - timedelta(days=args.days)
        while end > start_limit:
            start = max(start_limit, end - timedelta(days=WINDOW_DAYS))
            try:
                raw = client.intraday_history(
                    inst,
                    from_date=start.strftime("%Y-%m-%d 09:15:00"),
                    to_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                    interval=str(args.iv),
                )
                frame = chart_response_to_frame(raw)
                if not frame.empty:
                    counts = ingest_frame(key, str(args.iv), frame)
                    pulled_days += len(counts)
            except Exception as exc:  # noqa: BLE001 - keep walking on a bad window
                print(f"{key}: window {start.date()}..{end.date()} failed: {exc}")
            end = start - timedelta(seconds=1)
            time.sleep(max(0.0, args.sleep))
        cached = list_cached_days(key, str(args.iv))
        span = f"{cached[0]} .. {cached[-1]}" if cached else "none"
        print(f"{key}: {len(cached)} cached days after backfill ({span})")


if __name__ == "__main__":
    main()
