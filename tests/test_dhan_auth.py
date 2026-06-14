from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from index_ai.config import DhanSettings
from index_ai.dhan_auth import (
    _extract_access_token_value,
    _extract_consume_payload,
    _reject_consent_app_id_mistake,
    _store_pending_consent,
    auto_refresh_dhan_token,
    consume_consent,
    looks_like_jwt,
    normalize_token_id,
    oauth_redirect_urls,
    parse_oauth_callback_value,
    renew_access_token,
    save_token_from_user_input,
    token_renew_eligibility,
    token_renew_status,
)


def test_normalize_token_id_from_url() -> None:
    url = "http://127.0.0.1:3000/callback?tokenId=940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
    assert normalize_token_id(url) == "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"


def test_normalize_token_id_bare() -> None:
    assert normalize_token_id("abc-123") == "abc-123"


def test_normalize_token_id_query_only() -> None:
    assert normalize_token_id("tokenId=uuid-here") == "uuid-here"


def test_parse_oauth_callback_value_jwt() -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJkaGFuIn0."
        "signaturepart"
    )
    assert parse_oauth_callback_value(jwt) == jwt


def test_parse_oauth_callback_value_redirect() -> None:
    url = "http://127.0.0.1:8000/callback?tokenId=940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
    assert parse_oauth_callback_value(url) == "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"


def test_looks_like_jwt() -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJkaGFuIn0."
        "signaturepart"
    )
    assert looks_like_jwt(jwt)
    assert not looks_like_jwt("940b0ca1-3ff4-4476-b46e-03a3ce7dc55d")


def test_oauth_redirect_urls() -> None:
    urls = oauth_redirect_urls()
    assert "http://127.0.0.1:8000/dhan/oauth/callback" in urls
    assert "http://127.0.0.1:8000/callback" in urls


def test_extract_consume_payload_nested() -> None:
    data = {"data": {"accessToken": "tok", "dhanClientId": "1", "expiryTime": "x"}}
    inner = _extract_consume_payload(data)
    assert inner["accessToken"] == "tok"


def test_reject_consent_app_id_mistake(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "dhan_oauth_state.json"
    monkeypatch.setattr("index_ai.dhan_auth._OAUTH_STATE_PATH", state_file)
    consent = "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
    _store_pending_consent(consent, "1100426170")
    with pytest.raises(RuntimeError, match="consentAppId"):
        _reject_consent_app_id_mistake(consent)


def _settings() -> DhanSettings:
    return DhanSettings(
        client_id="1100426170",
        access_token="",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )


def test_consume_consent_saves_and_verifies(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("index_ai.dhan_auth._OAUTH_STATE_PATH", tmp_path / "oauth.json")
    token_id = "11111111-2222-4333-8444-555555555555"
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJkYW5DbGllbnRJZCI6IjExMDA0MjYxNzAifQ."
        "sig"
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "accessToken": jwt,
        "dhanClientId": "1100426170",
        "expiryTime": "2026-05-30T12:00:00",
        "dhanClientName": "TEST",
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_response

    with patch("index_ai.dhan_auth.httpx.Client", return_value=mock_client):
        with patch("index_ai.dhan_auth.finalize_token_save") as fin:
            fin.return_value = {"status": "saved", "message": "ok"}
            result = consume_consent(_settings(), token_id)

    assert result["status"] == "saved"
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    assert call_kwargs.kwargs["params"]["tokenId"] == token_id
    fin.assert_called_once()


def test_consume_consent_rejects_consent_app_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("index_ai.dhan_auth._OAUTH_STATE_PATH", tmp_path / "oauth.json")
    consent = "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
    _store_pending_consent(consent, "1100426170")
    with pytest.raises(RuntimeError, match="consentAppId"):
        save_token_from_user_input(_settings(), consent)


def test_validate_client_id_non_numeric() -> None:
    bad = DhanSettings(
        client_id="not-a-number",
        access_token="",
        api_base_url="https://api.dhan.co/v2",
        api_key="k",
        api_secret="s",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    with patch("index_ai.dhan_auth._auth_headers", return_value={"app_id": "k", "app_secret": "s"}):
        with pytest.raises(RuntimeError, match="numeric"):
            from index_ai.dhan_auth import generate_consent

            generate_consent(bad)


def test_auto_refresh_token_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    s = DhanSettings(
        client_id="1100426170",
        access_token="tok",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    monkeypatch.setenv("AUTO_RENEW_DHAN_TOKEN", "true")
    monkeypatch.setenv("AUTO_RENEW_THRESHOLD_MINUTES", "30")
    monkeypatch.setattr("index_ai.dhan_auth._token_seconds_left", lambda _s: 3600)
    out = auto_refresh_dhan_token(s, reason="test")
    assert out["attempted"] is False
    assert out["reason"] == "not_due"


def test_extract_access_token_value_renew_token_field() -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJkaGFuIn0."
        "signaturepart"
    )
    data = {
        "createTime": "2026-06-02T08:39:08.631",
        "expiryTime": "2026-06-03T08:39:08.629",
        "token": jwt,
    }
    assert _extract_access_token_value(data) == jwt


def test_renew_access_token_parses_token_field(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJkYW5DbGllbnRJZCI6IjExMDA0MjYxNzAifQ."
        "sig"
    )
    renewed = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJkYW5DbGllbnRJZCI6IjExMDA0MjYxNzAifQ."
        "renewed"
    )
    s = DhanSettings(
        client_id="1100426170",
        access_token=jwt,
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="2099-01-01T00:00:00",
    )
    monkeypatch.setenv("DHAN_TOKEN_SOURCE", "WEB")
    monkeypatch.setattr("index_ai.dhan_auth.reconcile_env_with_jwt", lambda: None)
    monkeypatch.setattr(
        "index_ai.dhan_auth.jwt_token_status",
        lambda _t: {"is_jwt": True, "dhan_client_id": "1100426170", "expired": False},
    )
    monkeypatch.setattr("index_ai.dhan_auth._token_seconds_left", lambda _s: 3600)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "createTime": "2026-06-02T08:39:08.631",
        "expiryTime": "2026-06-03T08:39:08.629",
        "token": renewed,
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_response

    with patch("index_ai.dhan_auth.httpx.Client", return_value=mock_client):
        with patch("index_ai.dhan_auth.finalize_token_save") as fin:
            fin.return_value = {"status": "saved", "message": "ok"}
            result = renew_access_token(s)

    assert result["status"] == "renewed"
    fin.assert_called_once()
    assert fin.call_args.kwargs["access_token"] == renewed
    assert fin.call_args.kwargs["expiry"] == "2026-06-03T08:39:08.629"


def test_extract_access_token_value_nested() -> None:
    data = {"accessToken": {"token": "eyJ.nested.sig"}}
    assert _extract_access_token_value(data) == "eyJ.nested.sig"


def test_extract_access_token_value_flat() -> None:
    data = {"accessToken": "flat-token"}
    assert _extract_access_token_value(data) == "flat-token"


def test_token_renew_eligibility_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHAN_TOKEN_SOURCE", "OAUTH")
    s = DhanSettings(
        client_id="1100426170",
        access_token="tok",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="2099-01-01T00:00:00",
    )
    info = token_renew_eligibility(s)
    assert info["renewable"] is False
    assert info["token_source"] == "OAUTH"
    assert "OAuth" in str(info["reason"])


def test_totp_credentials_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from index_ai.dhan_auth import totp_credentials_configured

    monkeypatch.delenv("DHAN_PIN", raising=False)
    monkeypatch.delenv("DHAN_TOTP_SECRET", raising=False)
    monkeypatch.setenv("DHAN_CLIENT_ID", "1100426170")
    assert totp_credentials_configured() is False
    monkeypatch.setenv("DHAN_PIN", "123456")
    monkeypatch.setenv("DHAN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    assert totp_credentials_configured() is True


def test_auto_refresh_skips_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DHAN_PIN", raising=False)
    monkeypatch.delenv("DHAN_TOTP_SECRET", raising=False)
    s = DhanSettings(
        client_id="1100426170",
        access_token="tok",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    monkeypatch.setenv("AUTO_RENEW_DHAN_TOKEN", "true")
    monkeypatch.setattr("index_ai.dhan_auth._token_seconds_left", lambda _s: 0)
    out = auto_refresh_dhan_token(s, force=True, reason="test_expired")
    assert out["attempted"] is False
    assert out["reason"] == "expired"


def test_auto_refresh_token_force_renews(monkeypatch: pytest.MonkeyPatch) -> None:
    s = DhanSettings(
        client_id="1100426170",
        access_token="tok",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    monkeypatch.setenv("AUTO_RENEW_DHAN_TOKEN", "true")
    monkeypatch.setenv("DHAN_TOKEN_SOURCE", "WEB")
    monkeypatch.setattr("index_ai.dhan_auth._token_seconds_left", lambda _s: 900)
    monkeypatch.setattr("index_ai.dhan_auth.renew_access_token", lambda _s: {"status": "renewed"})

    class _Cfg:
        def __init__(self, dhan):
            self.dhan = dhan

    monkeypatch.setattr("index_ai.config.settings", lambda: _Cfg(s))
    out = auto_refresh_dhan_token(s, force=True, reason="test_force")
    assert out["attempted"] is True
    assert out["renewed"] is True


def test_renew_access_token_rejects_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DHAN_TOKEN_SOURCE", "OAUTH")
    s = DhanSettings(
        client_id="1100426170",
        access_token="tok",
        api_base_url="https://api.dhan.co/v2",
        api_key="key",
        api_secret="secret",
        auth_base_url="https://auth.dhan.co",
        token_expiry="2099-01-01T00:00:00",
    )
    with pytest.raises(RuntimeError, match="OAuth"):
        renew_access_token(s)


def test_token_renew_status_reflects_disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_RENEW_DHAN_TOKEN", "false")
    status = token_renew_status()
    assert status["enabled"] is False
