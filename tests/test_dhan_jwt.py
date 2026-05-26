from __future__ import annotations

import base64
import json

from index_ai.dhan_auth import jwt_token_status, looks_like_jwt


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJ{header[3:]}.{body}.{body}"


def test_jwt_token_status_reads_client_id() -> None:
    token = _fake_jwt({"dhanClientId": "1100426170", "exp": 9999999999})
    assert looks_like_jwt(token)
    status = jwt_token_status(token)
    assert status["dhan_client_id"] == "1100426170"
    assert status["expired"] is False
