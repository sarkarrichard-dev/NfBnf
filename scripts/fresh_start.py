"""Fresh start — archive trade journals, trained models and cached reports for
one or more sections, then (index only) set the data epoch so nothing learns
from the old history again.

Richard, 2026-09-10: "I want to scrap historical data and just keep data from
live/paper trades that the algo is taking moving forward." (index)
Richard, 2026-09-12: "clean up crypto history and all running orders, also
the futures and commodities so that from the next restart all three will
start fresh" — while leaving the already-reset index side alone.

Kept always, in every section: market-data caches (candles, measured spreads,
OI, VIX, the resolved contract/universe caches). Those are observations of
the market, not the algo's own record.

    python -m scripts.fresh_start                                    # dry run, everything
    python -m scripts.fresh_start --sections crypto,futures,commodities
    python -m scripts.fresh_start --sections crypto,futures,commodities --commit

Refuses to run while the server holds memory/.server.lock, or while a LIVE
position is open in a section being reset (index or crypto only — futures
and commodities have no live path).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from index_ai.config import MEMORY_DIR
from index_ai.data_epoch import set_data_epoch

# Section -> files/dirs under memory/ that are that section's own trade
# record (archived on a reset of that section). A "models/x" entry is one
# file inside the shared memory/models/ dir, not the whole directory — index
# and crypto each keep their own model there and must not disturb the other's.
_GROUPS: dict[str, list[str]] = {
    "index": [
        "trade_memory.sqlite", "trade_memory.sqlite-wal", "trade_memory.sqlite-shm",
        "options_cpr_journal.jsonl", "options_cpr_paper.json",
        "day_review.json", "daily_ops.json", "daily_reports",
        "oi_snapshots.json", "participant_oi.json",
        "models/brain_model.joblib", "models/brain_meta.json",
        "hf", "datasets",
    ],
    "crypto": [
        "crypto_journal.jsonl", "crypto_state.json", "crypto_strategy_params.json",
        "crypto_day_review.json", "crypto_day_summary.txt", "crypto_alert_stamps.json",
        "models/crypto_model.joblib", "models/crypto_meta.json",
    ],
    "futures": ["futures_paper.json", "futures_journal.jsonl"],
    "commodities": ["commodity_journal.jsonl", "commodity_state.json"],
}
_ALL_SECTIONS = list(_GROUPS)
# empty JSONL journals to recreate per section after a reset of that section
_EMPTY_JOURNALS: dict[str, list[str]] = {
    "crypto": ["crypto_journal.jsonl"],
    "futures": ["futures_journal.jsonl"],
    "commodities": ["commodity_journal.jsonl"],
}
_ARCHIVE_GLOBS = {"index": ["trade_memory.sqlite.bak-*"]}


class _SafetyProbeFailed(RuntimeError):
    """A pre-flight check could not run — treat as unsafe, never as clear."""


def _live_positions_open(sections: list[str]) -> list[str]:
    """Any unresolved LIVE position in a section being reset. Raises rather
    than returning [] if a probe can't run (e.g. the DB is locked)."""
    out: list[str] = []
    if "index" in sections:
        try:
            from index_ai.learning import open_trades

            for t in open_trades():  # pnl IS NULL, not rejected/failed
                if str(t.get("mode") or "").upper() == "LIVE" or str(
                    t.get("status") or ""
                ).upper().startswith("LIVE"):
                    out.append(f"index {t.get('instrument')} {t.get('action')} ({t.get('status')})")
        except Exception as exc:
            raise _SafetyProbeFailed(f"could not read the index journal: {exc}") from exc
    if "crypto" in sections:
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


def _targets(sections: list[str]) -> list[Path]:
    found: list[Path] = []
    for section in sections:
        for name in _GROUPS[section]:
            p = MEMORY_DIR / name
            if p.exists():
                found.append(p)
        for pat in _ARCHIVE_GLOBS.get(section, []):
            found.extend(sorted(MEMORY_DIR.glob(pat)))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true", help="actually move the files")
    ap.add_argument(
        "--sections",
        default=",".join(_ALL_SECTIONS),
        help=f"comma list to reset, any of {_ALL_SECTIONS} (default: all)",
    )
    args = ap.parse_args(argv)

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    unknown = [s for s in sections if s not in _GROUPS]
    if unknown:
        print(f"Unknown section(s): {unknown}. Valid: {_ALL_SECTIONS}")
        return 1
    if not sections:
        print("Nothing to do — empty --sections.")
        return 0

    if _server_running():
        print("REFUSING — a server is running (holds memory/.server.lock).")
        print("Stop it first: the launcher's \"Stop server\", or kill the "
              "`python -m index_ai.server` / uvicorn process. Then run this, then restart.")
        return 1

    try:
        live = _live_positions_open(sections)
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

    targets = _targets(sections)
    from index_ai.market_clock import now_ist

    archive = MEMORY_DIR / "archive" / now_ist().strftime("%Y-%m-%dT%H%M%S")

    print(f"Sections    : {sections}")
    print(f"Archive dir : {archive}")
    if "index" in sections:
        print(f"Data epoch  : will be set to now ({now_ist().isoformat()})")
    else:
        print("Data epoch  : left untouched (index not in scope) — new trades in the "
              "reset sections already postdate it")
    print(f"To archive  : {len(targets)} items")
    for p in targets:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        rel = p.relative_to(MEMORY_DIR)
        print(f"  · {rel}  ({size / 1024:.0f} KB)")

    if not args.commit:
        print("\nDry run. Re-run with --commit to do it.")
        return 0

    import gc

    gc.collect()  # drop any sqlite handle the safety probes left open

    archive.mkdir(parents=True, exist_ok=True)
    # copy everything first, verify, then delete the originals — so a locked
    # file can't leave us with a half-archive. Preserve the relative path (a
    # "models/x.joblib" entry lands at archive/<ts>/models/x.joblib).
    for p in targets:
        rel = p.relative_to(MEMORY_DIR)
        dst = archive / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir():
            shutil.copytree(p, dst)
        else:
            shutil.copy2(p, dst)
        if not dst.exists():
            print(f"ABORT — copy of {rel} did not land. Nothing deleted.")
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
            stuck.append(str(p.relative_to(MEMORY_DIR)))

    if stuck:
        print(f"\nArchived OK to {archive}, but could not delete: {', '.join(stuck)}")
        print("Something still has them open. The archive is complete — delete the "
              "originals manually, or reboot. NOT setting the data epoch until the "
              "originals are gone (a stale trade_memory.sqlite would be read again).")
        return 1

    for section in sections:
        for name in _EMPTY_JOURNALS.get(section, []):
            (MEMORY_DIR / name).touch()

    if "index" in sections:
        epoch = set_data_epoch()
        from index_ai.learning import init_db  # recreate an empty trades DB with the right schema

        init_db()
        print(f"Data epoch set: {epoch}")
        print("Fresh trade_memory.sqlite created.")

    print(f"\nDone. {len(targets)} items -> {archive}")
    print(f"Sections reset: {sections}")
    print("Start the server now — those lanes log into empty journals from here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
