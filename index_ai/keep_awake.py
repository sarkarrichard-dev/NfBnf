"""Ask Windows not to put the PC to sleep while the trading server is running.

The scanner, the tick feed and the crypto lane all stop when the machine sleeps, which
leaves holes in the recorded prices, option chains and spreads (the log shows stretches
of 25-40 minutes of silence in market hours). The screen may still turn off; only
*sleep* is held back. It is released the moment the server stops. Windows only, and
switched off with ``KEEP_AWAKE=false``.
"""

from __future__ import annotations

import os
import sys
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def enabled() -> bool:
    return os.getenv("KEEP_AWAKE", "true").strip().lower() in {"1", "true", "yes", "on"}


def _set_state(flags: int) -> None:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]


def _hold(stop: threading.Event) -> None:
    # The request belongs to this thread, so the thread stays alive until `stop` is set.
    try:
        _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        stop.wait()
    finally:
        try:
            _set_state(ES_CONTINUOUS)
        except Exception:
            pass


def start() -> threading.Event | None:
    """Begin holding the PC awake. Returns the event to set on shutdown, or None when
    this is not Windows or the feature is switched off."""
    if sys.platform != "win32" or not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=_hold, args=(stop,), name="keep-awake", daemon=True).start()
    return stop


def stop(handle: threading.Event | None) -> None:
    if handle is not None:
        handle.set()
