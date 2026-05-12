from __future__ import annotations

from dataclasses import dataclass

from trading_ai_engine.secrets_bridge import env_or_local


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_base_url: str
    openai_chat_model: str


def get_settings() -> Settings:
    base = env_or_local("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1"
    return Settings(
        openai_api_key=env_or_local("OPENAI_API_KEY"),
        openai_base_url=base.rstrip("/"),
        openai_chat_model=env_or_local("OPENAI_CHAT_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
    )
