"""Refuse to start a second server against the same memory/ directory.

Two ``index_ai.server`` processes silently share one ``.env`` and one set of
SQLite databases. The in-process ``threading.Lock`` around ``.env`` writes does
nothing across processes, and two scanners / two tick feeds then contend on the
databases' OS-level file locks — every ``open_trades_for_mode`` read (and so
every Paper/Live switch and lot change) blocks for seconds behind the other
process's write. Worse, on a trading day both scanners could place orders.

This takes an OS advisory lock on a file in ``memory/`` for the process
lifetime. The OS drops it on any exit, clean or crash, so there is no stale-lock
problem and no PID-liveness guessing.
"""

from __future__ import annotations

import os
import sys

from index_ai.config import MEMORY_DIR

_LOCK_PATH = MEMORY_DIR / ".server.lock"
_handle = None  # kept alive for the process lifetime; releasing it releases the lock

_MESSAGE = (
    "Another Index Options AI server is already running against this folder.\n"
    "Two instances share one .env and one database and stall each other — that is\n"
    "the Paper/Live and lot-size lag. Stop the other python -m index_ai.server\n"
    '(the launcher\'s "Stop server", or Task Manager), and check your IDE is not\n'
    "running one too."
)


def acquire_or_exit() -> None:
    """Take the lock, or print why we can't and exit non-zero."""
    global _handle
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _handle = open(_LOCK_PATH, "a+")  # noqa: SIM115 — held (and locked) for the whole run
    try:
        if sys.platform == "win32":
            import msvcrt

            _handle.seek(0)  # lock byte 0 so both processes contend on the same region
            msvcrt.locking(_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _handle.close()
        _handle = None
        raise SystemExit(_MESSAGE)


if __name__ == "__main__":  # self-check: second acquire in a child process fails
    import subprocess
    import textwrap

    acquire_or_exit()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                "from index_ai.single_instance import acquire_or_exit; acquire_or_exit()"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert child.returncode != 0, "second instance was allowed to start"
    assert "already running" in child.stderr, child.stderr
    print("single_instance.py self-check ok — second instance refused")
