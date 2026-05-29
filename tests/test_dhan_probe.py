from __future__ import annotations

from index_ai.config import DhanSettings
from index_ai.dhan_auth import probe_access_token, save_access_token_direct


def _settings(access_token: str = "old") -> DhanSettings:
    return DhanSettings(
        client_id="1100426170",
        access_token=access_token,
        api_base_url="https://api.dhan.co/v2",
        api_key="k",
        api_secret="s",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )


def test_save_token_succeeds_when_profile_fails_but_market_ok(monkeypatch, tmp_path) -> None:
    jwt = (
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJkaGFuIiwiZGhhbkNsaWVudElkIjoiMTEwMDQyNjE3MCIsImV4cCI6OTk5OTk5OTk5OX0."
        "sig"
    )
    env = tmp_path / ".env"
    env.write_text("DHAN_CLIENT_ID=1100426170\n", encoding="utf-8")
    monkeypatch.setattr("index_ai.config.ENV_PATH", env)
    monkeypatch.setattr(
        "index_ai.dhan_auth.probe_access_token",
        lambda s: {
            "token_ok": True,
            "profile_ok": False,
            "charts_ok": False,
            "market_ok": True,
            "profile": {},
        },
    )
    result = save_access_token_direct(_settings(), jwt)
    assert result["status"] == "saved"
    assert "option-chain" in result["message"]


def test_probe_raises_when_market_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "index_ai.dhan_auth.probe_access_token",
        lambda s: (_ for _ in ()).throw(RuntimeError("market: rejected")),
    )
    import pytest

    with pytest.raises(RuntimeError, match="market"):
        probe_access_token(_settings("bad"))
