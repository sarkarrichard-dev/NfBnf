"""Playwright browser check for the static dashboard (requires live uvicorn)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[1]
_PORT = 18765
_BASE = f"http://127.0.0.1:{_PORT}"


@pytest.fixture(scope="module")
def live_dashboard_server():
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("Set RUN_E2E=1 to run Playwright dashboard E2E")
    pytest.importorskip("playwright", reason="pip install -e \".[e2e]\" and playwright install chromium")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "trading_ai_engine.server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(_PORT),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"{_BASE}/api/health", timeout=1.0)
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.25)
        else:
            pytest.fail("uvicorn did not become ready in time")
        yield _BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_dashboard_home_loads(live_dashboard_server: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{live_dashboard_server}/", wait_until="domcontentloaded", timeout=30_000)
            assert "Trading AI" in page.title() or "Workstation" in (page.title() or "")
            body = page.locator("body")
            assert body.count() == 1
            assert page.locator("#server-pill").count() == 1
        finally:
            browser.close()
