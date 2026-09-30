"""Every fixture under tests/fixtures/broker_traffic/ must be a genuinely
redacted recording (D-03 / T-02-01) — no API key, secret, signature, token or
account identifier survives, and every row states where it came from."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.capture_broker_traffic import REDACT_KEYS

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "broker_traffic"

_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _walk(obj):
    """Yield (key_or_None, value) for every scalar reachable in obj, and every
    (key, value) pair at every dict depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _jsonl_files() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(FIXTURES_DIR.glob("*.jsonl"))


def test_fixtures_directory_has_at_least_one_file():
    assert _jsonl_files(), f"no *.jsonl fixtures found under {FIXTURES_DIR}"


def test_every_row_is_redacted_and_well_formed():
    files = _jsonl_files()
    assert files, "no fixtures to check"
    for path in files:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines, f"{path} is empty"
        for i, line in enumerate(lines, start=1):
            row = json.loads(line)  # must parse
            assert row.get("source") in {"captured", "journal", "reference"}, (
                f"{path}:{i} has invalid source {row.get('source')!r}"
            )
            for key, value in _walk(row):
                if key is not None and str(key).lower() in REDACT_KEYS:
                    assert value == "REDACTED", f"{path}:{i} key {key!r} is not redacted"
                if isinstance(value, str):
                    assert not _JWT_RE.match(value), f"{path}:{i} contains a JWT-shaped string"
                    assert not _HEX64_RE.match(value), (
                        f"{path}:{i} contains a 64-hex-char string (looks like a signature)"
                    )
            if row.get("source") == "captured" and row.get("method"):
                assert row["method"] == "GET", (
                    f"{path}:{i} is a captured row with a non-GET method {row['method']!r}"
                )
