"""Off-machine backup of the irreplaceable local state to S3.

The app runs on one Windows PC. Its trade journal, market-observation log and
trained ML models live only in the gitignored ``memory/`` directory — if that
disk dies, the entire trading history and everything the brain has learned is
gone. This module copies just that state to an S3 bucket.

Opt-in. It does nothing unless ``ENABLE_S3_BACKUP=true`` and ``S3_BACKUP_BUCKET``
is set in ``.env``. It runs automatically from ``daily_ops.run_eod()`` after the
post-close report, and can be run by hand:

    python -m index_ai.cloud_backup

Only non-regenerable state goes up: the two SQLite databases (copied with
SQLite's online-backup API so a live writer can't tear the file), the trained
models, the daily reports, and the small market-context JSON caches. Candle CSVs,
datasets and logs are excluded — they are large and can be re-fetched.

Turn on **versioning** on the bucket: every run overwrites the same keys, so
versioning is what gives you point-in-time recovery, for free, with no extra code
here.

Credentials come from the standard AWS chain (``aws configure`` writes
``~/.aws/credentials``); this module never reads them from ``.env``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist

# memory-relative globs that are worth keeping. SQLite files are handled
# separately (they need a consistent snapshot), everything else is copied as-is.
_SQLITE = ("trade_memory.sqlite", "market_log.sqlite")
_GLOBS = (
    "models/**/*",
    "daily_reports/*",
    "market_context.json",
    "participant_oi.json",
    "vix.json",
    "spread_skips.json",
    "ai_commentary.json",
    "daily_ops.json",
    "learned_settings.json",
)


def backup_enabled() -> bool:
    return os.getenv("ENABLE_S3_BACKUP", "false").strip().lower() in {"1", "true", "yes", "on"}


def _bucket() -> str:
    return os.getenv("S3_BACKUP_BUCKET", "").strip()


def _prefix() -> str:
    return os.getenv("S3_BACKUP_PREFIX", "algo-bnf").strip().strip("/")


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a possibly-live SQLite db via the online-backup API."""
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def _files_to_upload(staging: Path) -> list[tuple[Path, str]]:
    """(local path, S3 key-relative-to-prefix) pairs."""
    pairs: list[tuple[Path, str]] = []

    for name in _SQLITE:
        src = MEMORY_DIR / name
        if src.is_file():
            snap = staging / name
            _snapshot_sqlite(src, snap)
            pairs.append((snap, name))

    for pattern in _GLOBS:
        for path in MEMORY_DIR.glob(pattern):
            if path.is_file():
                pairs.append((path, path.relative_to(MEMORY_DIR).as_posix()))

    return pairs


def run_backup() -> dict[str, Any]:
    """Best-effort. Never raises — the caller is a post-close housekeeping step."""
    out: dict[str, Any] = {"at": now_ist().isoformat(timespec="seconds"), "uploaded": 0}

    if not backup_enabled():
        return {**out, "skipped": "ENABLE_S3_BACKUP is off"}
    bucket = _bucket()
    if not bucket:
        return {**out, "skipped": "S3_BACKUP_BUCKET is not set"}

    try:
        import boto3  # optional dep: pip install boto3
    except ImportError:
        return {**out, "error": "boto3 not installed — run: pip install boto3"}

    prefix = _prefix()
    client = boto3.client("s3")
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="algo-bnf-backup-") as tmp:
        staging = Path(tmp)
        try:
            pairs = _files_to_upload(staging)
        except Exception as exc:  # snapshot failure — nothing uploaded
            return {**out, "error": f"staging failed: {str(exc)[:200]}"}

        for local, rel in pairs:
            key = f"{prefix}/{rel}" if prefix else rel
            try:
                client.upload_file(str(local), bucket, key)
                out["uploaded"] += 1
            except Exception as exc:
                errors.append(f"{rel}: {str(exc)[:160]}")

    out["target"] = f"s3://{bucket}/{prefix}".rstrip("/")
    if errors:
        out["errors"] = errors[:10]
    return out


if __name__ == "__main__":  # manual run / self-check
    if backup_enabled() and _bucket():
        import json

        print(json.dumps(run_backup(), indent=2, default=str))
    else:
        # exercise staging without touching the network
        with tempfile.TemporaryDirectory() as tmp:
            pairs = _files_to_upload(Path(tmp))
            rels = {r for _, r in pairs}
            assert all(p.is_file() for p, _ in pairs)
            assert "candles" not in " ".join(rels) and "server.log" not in rels
            if (MEMORY_DIR / "trade_memory.sqlite").is_file():
                assert "trade_memory.sqlite" in rels
        print(
            f"cloud_backup.py self-check ok — {len(pairs)} file(s) would upload, "
            f"set ENABLE_S3_BACKUP=true and S3_BACKUP_BUCKET to run for real"
        )
