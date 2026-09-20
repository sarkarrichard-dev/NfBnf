"""Optional shared-secret gate for the endpoints that can move real money.

Known open gap (see memory/project-algo-bnf-vision.md): the FastAPI API has
no authentication at all, and it can arm live orders. Full auth is really
part of the multi-tenancy build-out (per-user accounts, not a single shared
secret) — this is the cheap interim stopgap: a shared secret that closes the
sharpest edge (arming/disarming real orders, switching PAPER<->LIVE) before
that larger system exists.

Off by default. The current deployment runs on Richard's own Tailscale
network (see memory/tailnet-team-access.md) — requiring a secret that
doesn't exist yet in .env would lock him out of his own arm/disarm flow the
moment this ships. Set ADMIN_API_SECRET in .env to turn the gate on; leaving
it unset keeps today's behavior exactly as it is.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

ADMIN_SECRET_HEADER = "X-Admin-Secret"


def require_admin_secret(request: Request) -> None:
    expected = os.getenv("ADMIN_API_SECRET", "").strip()
    if not expected:
        return  # opt-in, same pattern as every other off-by-default flag here
    got = request.headers.get(ADMIN_SECRET_HEADER, "")
    if got != expected:
        raise HTTPException(403, "Missing or incorrect admin secret.")


if __name__ == "__main__":  # self-check

    class _FakeRequest:
        def __init__(self, headers: dict[str, str]):
            self.headers = headers

    # unset -> always passes, regardless of headers
    os.environ.pop("ADMIN_API_SECRET", None)
    require_admin_secret(_FakeRequest({}))  # no raise

    # set -> wrong/missing header rejected, correct header passes
    os.environ["ADMIN_API_SECRET"] = "s3cr3t"
    try:
        require_admin_secret(_FakeRequest({}))
        raise AssertionError("expected 403 with no header")
    except HTTPException as exc:
        assert exc.status_code == 403
    try:
        require_admin_secret(_FakeRequest({ADMIN_SECRET_HEADER: "wrong"}))
        raise AssertionError("expected 403 with wrong header")
    except HTTPException as exc:
        assert exc.status_code == 403
    require_admin_secret(_FakeRequest({ADMIN_SECRET_HEADER: "s3cr3t"}))  # no raise
    os.environ.pop("ADMIN_API_SECRET", None)
    print("admin_auth.py self-check ok")
