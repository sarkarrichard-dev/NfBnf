"""Fresh start — archive every trade journal, trained model and cached report,
then set the data epoch so nothing learns from the old history again.

Richard, 2026-09-10: "I want to scrap historical data and just keep data from
live/paper trades that the algo is taking moving forward."

Kept: market-data caches (candles, measured spreads, OI, VIX), auth, config.
Those are observations of the market, not the algo's own record.

    python -m scripts.fresh_start            # dry run — shows what would move
    python -m scripts.fresh_start --commit   # do it

Refuses to run while a LIVE index or crypto position is open.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from index_ai.config import MEMORY_DIR
from index_ai.data_epoch import set_data_epoch

# files / dirs under memory/ that are the algo's own record — archived on a reset
_TRADE_ARTIFACTS = [
    "trade_memory.sqlite",
    "trade_memory.sqlite-wal",
    "trade_memory.sqlite-shm",
    "crypto_journal.jsonl",
    "crypto_state.json",
    "futures_paper.json",
    "futures_journal.jsonl",
    "options_cpr_journal.jsonl",
    "options_cpr_paper.json",
    "crypto_strategy_params.json",       # nightly-tuned params → back to code defaults
    "models",                            # the 3 trained models
    "datasets",                          # ML training datasets
    "hf",
    "day_review.json",
    "crypto_day_review.json",
    "daily_ops.json",
    "daily_reports",
    "oi_snapshots.json",
    "participant_oi.json",
]
_TRADE_ARTIFACT_GLOBS = ["trade_memory.sqlite.bak-*"]


def _live_positions_open() -> list[str]:
    out: list[str] = []
    try:
        from index_ai.learning import recent_trades

        for t in recent_trades(limit=500):
            if t.get("pnl") is None and str(t.get("status") or "").upper() == "LIVE_TRADED":
                out.append(f"index {t.get('instrument')} {t.get('action')}")
    except Exception:
        pass
    try:
        from crypto.day_review import open_positions

        for p in open_positions():
            if str(p.get("mode") or "").lower() == "live":
                out.append(f"crypto {p.get('asset')} {p.get('side')}")
    except Exception:
        pass
    return out


def _targets() -> list[Path]:
    found: list[Path] = []
    for name in _TRADE_ARTIFACTS:
        p = MEMORY_DIR / name
        if p.exists():
            found.append(p)
    for pat in _TRADE_ARTIFACT_GLOBS:
        found.extend(sorted(MEMORY_DIR.glob(pat)))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="actually move the files")
    args = ap.parse_args(argv)

    live = _live_positions_open()
    if live:
        print("REFUSING — live positions are open:")
        for x in live:
            print(f"  · {x}")
        print("Close them (or switch to Paper) first.")
        return 1

    targets = _targets()
    from index_ai.market_clock import now_ist

    archive = MEMORY_DIR / "archive" / now_ist().strftime("%Y-%m-%dT%H%M%S")

    print(f"Archive dir : {archive}")
    print(f"Data epoch  : will be set to now ({now_ist().isoformat()})")
    print(f"To archive  : {len(targets)} items")
    for p in targets:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        print(f"  · {p.name}  ({size / 1024:.0f} KB)")

    if not args.commit:
        print("\nDry run. Re-run with --commit to do it.")
        return 0

    archive.mkdir(parents=True, exist_ok=True)
    for p in targets:
        shutil.move(str(p), str(archive / p.name))
    epoch = set_data_epoch()

    # recreate an empty trades DB with the right schema so the app starts clean
    from index_ai.learning import init_db

    init_db()
    (MEMORY_DIR / "crypto_journal.jsonl").touch()

    print(f"\nDone. {len(targets)} items → {archive}")
    print(f"Data epoch set: {epoch}")
    print("Fresh trade_memory.sqlite + crypto_journal.jsonl created. The lanes "
          "keep running; they now log into empty journals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
