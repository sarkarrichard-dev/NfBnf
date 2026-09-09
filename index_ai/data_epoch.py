"""The data epoch — the moment we stopped trusting the accumulated history and
started collecting fresh.

``memory/data_epoch.txt`` holds one ISO-8601 timestamp (IST). Everything the
algo learns from — the scorecard, the ML tuner, the day reviews — should count
only trades at or after it. Written by ``scripts/fresh_start.py``; absent means
"no epoch set, use everything".
"""

from __future__ import annotations

from datetime import datetime

from index_ai.config import MEMORY_DIR

_PATH = MEMORY_DIR / "data_epoch.txt"


def data_epoch() -> str | None:
    """The epoch as a stored ISO string, or None if unset."""
    if not _PATH.is_file():
        return None
    raw = _PATH.read_text(encoding="utf-8").strip()
    return raw or None


def data_epoch_dt() -> datetime | None:
    raw = data_epoch()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def set_data_epoch(when: datetime | None = None) -> str:
    from index_ai.market_clock import now_ist

    ts = (when or now_ist()).isoformat()
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(ts, encoding="utf-8")
    return ts


if __name__ == "__main__":  # self-check — round-trip, no clobber of a real epoch
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    import index_ai.data_epoch as m

    m._PATH = d / "data_epoch.txt"
    assert m.data_epoch() is None
    ts = m.set_data_epoch()
    assert m.data_epoch() == ts and m.data_epoch_dt() is not None
    print("index_ai.data_epoch self-check ok —", ts)
