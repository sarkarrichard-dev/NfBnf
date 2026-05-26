from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from index_ai.config import ENV_PATH, repair_env_access_token_line, update_env_values


def test_repair_splits_multiline_jwt(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    full = "eyJ" + "a" * 350 + ".sig"
    part1 = full[:120]
    part2 = full[120:240]
    part3 = full[240:]
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                f"DHAN_ACCESS_TOKEN={part1}",
                part2,
                part3,
                "DHAN_CLIENT_ID=1100426170",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("index_ai.config.ENV_PATH", env_file)
    assert repair_env_access_token_line() is True
    load_dotenv(env_file, override=True)
    import os

    loaded = os.getenv("DHAN_ACCESS_TOKEN", "")
    assert loaded == full
    assert len(loaded) > 300


def test_update_env_quotes_long_token(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DHAN_CLIENT_ID=1\n", encoding="utf-8")
    monkeypatch.setattr("index_ai.config.ENV_PATH", env_file)
    token = "eyJ" + "x" * 400
    update_env_values({"DHAN_ACCESS_TOKEN": token})
    text = env_file.read_text(encoding="utf-8")
    assert 'DHAN_ACCESS_TOKEN="' in text
    assert "\nxx" not in text  # no broken mid-token newline from our writer
    load_dotenv(env_file, override=True)
    import os

    assert os.getenv("DHAN_ACCESS_TOKEN") == token
