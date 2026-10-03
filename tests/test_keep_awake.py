from __future__ import annotations

import sys
import time

import pytest

from index_ai import keep_awake


def test_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("KEEP_AWAKE", "false")
    assert keep_awake.start() is None


def test_not_windows_does_nothing(monkeypatch):
    monkeypatch.setenv("KEEP_AWAKE", "true")
    monkeypatch.setattr(sys, "platform", "linux")
    assert keep_awake.start() is None


def test_holds_then_releases(monkeypatch):
    calls: list[int] = []
    monkeypatch.setenv("KEEP_AWAKE", "true")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(keep_awake, "_set_state", lambda flags: calls.append(flags))
    handle = keep_awake.start()
    assert handle is not None
    deadline = time.time() + 2
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls == [keep_awake.ES_CONTINUOUS | keep_awake.ES_SYSTEM_REQUIRED]
    keep_awake.stop(handle)
    deadline = time.time() + 2
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert calls[-1] == keep_awake.ES_CONTINUOUS  # released: back to normal sleep rules


def test_stop_none_is_fine():
    keep_awake.stop(None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_real_call_does_not_raise():
    keep_awake._set_state(keep_awake.ES_CONTINUOUS)  # a no-op request: clears nothing
