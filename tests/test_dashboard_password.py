"""The password gate is configured at import, so each case runs in its own
Python process with the env it needs (and never touches the real .env)."""

import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(code: str, **env: str) -> subprocess.CompletedProcess:
    # PYTEST_CURRENT_TEST makes config._load_env() skip the real .env, so these
    # env values are what the server sees (and no real secret leaks in)
    full = {**os.environ, "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
            "PYTEST_CURRENT_TEST": "subprocess", **env}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=full,
                          capture_output=True, text=True, timeout=180)


def test_public_deploy_refuses_to_start_without_a_password():
    r = _run("import index_ai.server", PUBLIC_DEPLOY="true", DASHBOARD_PASSWORD="")
    assert r.returncode != 0 and "DASHBOARD_PASSWORD" in r.stderr


def test_gate_health_open_wrong_password_locked_out():
    r = _run("""
        from fastapi.testclient import TestClient
        from index_ai.server import app
        c = TestClient(app)
        assert c.get("/api/health").status_code == 200            # probe stays open
        assert c.get("/api/status").status_code == 401            # no password
        assert c.get("/api/crypto/status", auth=("x", "correct-horse-battery")).status_code != 401
        for _ in range(10):
            assert c.get("/api/status", auth=("x", "wrong")).status_code == 401
        assert c.get("/api/status", auth=("x", "wrong")).status_code == 429
        assert c.get("/api/status", auth=("x", "correct-horse-battery")).status_code == 429
        print("ok")
    """, DASHBOARD_PASSWORD="correct-horse-battery", PUBLIC_DEPLOY="true",
         ALLOWED_HOSTS="testserver")
    assert r.returncode == 0, r.stderr[-2000:]
