from __future__ import annotations

from index_ai.dhan_auth import looks_like_jwt, normalize_token_id


def test_normalize_token_id_from_url() -> None:
    url = "http://127.0.0.1:3000/callback?tokenId=940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
    assert normalize_token_id(url) == "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"


def test_normalize_token_id_bare() -> None:
    assert normalize_token_id("abc-123") == "abc-123"


def test_normalize_token_id_query_only() -> None:
    assert normalize_token_id("tokenId=uuid-here") == "uuid-here"


def test_looks_like_jwt() -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJkaGFuIn0."
        "signaturepart"
    )
    assert looks_like_jwt(jwt)
    assert not looks_like_jwt("940b0ca1-3ff4-4476-b46e-03a3ce7dc55d")
