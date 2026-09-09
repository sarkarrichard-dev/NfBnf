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
import time
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


class _SafetyProbeFailed(RuntimeError):
    """A pre-flight check could not run — treat as unsafe, never as clear."""


def _live_positions_open() -> list[str]:
    """Any unresolved LIVE position — index or crypto. Raises rather than
    returning [] if a probe can't run (e.g. the DB is locked by the server)."""
    out: list[str] = []
    try:
        from index_ai.learning import open_trades

        for t in open_trades():  # pnl IS NULL, not rejected/failed
            if str(t.get("mode") or "").upper() == "LIVE" or str(t.get("status") or "").upper().startswith(
                "LIVE"
            ):
                out.append(f"index {t.get('instrument')} {t.get('action')} ({t.get('status')})")
    except Exception as exc:
        raise _SafetyProbeFailed(f"could not read the index journal: {exc}") from exc
    try:
        from crypto.day_review import open_positions

        for p in open_positions():
            if str(p.get("mode") or "").lower() == "live":
                out.append(f"crypto {p.get('asset')} {p.get('side')}")
    except Exception as exc:
        raise _SafetyProbeFailed(f"could not read the crypto state: {exc}") from exc
    return out


def _server_running() -> bool:
    """True if a uvicorn server holds the OS lock on memory/.server.lock. Moving
    the SQLite file out from under its open connection corrupts it, so we refuse."""
    lock = MEMORY_DIR / ".server.lock"
    if not lock.is_file():
        return False
    try:
        fh = open(lock, "a+")  # noqa: SIM115
    except OSError:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False  # we got the lock → no server
    except OSError:
        return True  # someone else holds it
    finally:
        fh.close()


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

    if _server_running():
        print("REFUSING — a server is running (holds memory/.server.lock).")
        print("Stop it first: the launcher's \"Stop server\", or kill the "
              "`python -m index_ai.server` / uvicorn process. Then run this, then restart.")
        return 1

    try:
        live = _live_positions_open()
    except _SafetyProbeFailed as exc:
        print(f"REFUSING — a safety check could not run: {exc}")
        print("A check that cannot run is not a check that passed. Resolve it and retry.")
        return 1
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

    import gc

    gc.collect()  # drop any sqlite handle the safety probes left open

    archive.mkdir(parents=True, exist_ok=True)
    # copy everything first, verify, then delete the originals — so a locked
    # file can't leave us with a half-archive.
    for p in targets:
        dst = archive / p.name
        if p.is_dir():
            shutil.copytree(p, dst)
        else:
            shutil.copy2(p, dst)
        if not dst.exists():
            print(f"ABORT — copy of {p.name} did not land. Nothing deleted.")
            return 1

    stuck: list[str] = []
    for p in targets:
        for attempt in range(4):
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                break
            except PermissionError:
                gc.collect()
                time.sleep(0.5)
        else:
            stuck.append(p.name)

    if stuck:
        print(f"\nArchived OK to {archive}, but could not delete: {', '.join(stuck)}")
        print("Something still has them open. The archive is complete — delete the "
              "originals manually, or reboot. NOT setting the data epoch until the "
              "originals are gone (a stale trade_memory.sqlite would be read again).")
        return 1

    epoch = set_data_epoch()

    # recreate an empty trades DB with the right schema so the app starts clean
    from index_ai.learning import init_db

    init_db()
    (MEMORY_DIR / "crypto_journal.jsonl").touch()

    print(f"\nDone. {len(targets)} items → {archive}")
    print(f"Data epoch set: {epoch}")
    print("Fresh trade_memory.sqlite + crypto_journal.jsonl created.")
    print("Start the server now — the lanes log into the empty journals from here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
