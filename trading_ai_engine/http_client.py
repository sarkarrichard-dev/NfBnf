from __future__ import annotations

from typing import Any

import httpx


def client(timeout_s: float = 60.0) -> httpx.Client:
    return httpx.Client(timeout=timeout_s)


def get_json(url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
    with client() as c:
        r = c.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    with client() as c:
        r = c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()
