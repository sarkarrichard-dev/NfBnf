from __future__ import annotations

import pytest

from trading_ai_engine.security_http import validate_https_public_url

pytestmark = [pytest.mark.unit, pytest.mark.security]


def test_validate_https_public_url_accepts_openai() -> None:
    u = validate_https_public_url("https://api.openai.com/v1")
    assert u == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://api.openai.com/v1",
        "https://127.0.0.1/v1",
        "https://localhost/foo",
        "https://192.168.1.1/",
    ],
)
def test_validate_https_public_url_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_https_public_url(raw, purpose="test")
