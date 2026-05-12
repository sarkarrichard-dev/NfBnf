"""
Local API keys and tokens (optional).

1. Copy this file to ``local_secrets.py`` in the same folder.
2. Fill in values you want to keep out of ``.env``.
3. ``local_secrets.py`` is gitignored — do not commit it.

Precedence: real environment variables (including from ``.env`` via ``load_dotenv``) always win
over attributes defined here.
"""

from __future__ import annotations

# --- OpenAI-compatible ---
OPENAI_API_KEY: str | None = None
OPENAI_BASE_URL: str | None = None  # e.g. "https://api.openai.com/v1"
OPENAI_CHAT_MODEL: str | None = None

# --- Dhan ---
DHAN_CLIENT_ID: str | None = None
DHAN_ACCESS_TOKEN: str | None = None
DHAN_FEED_URL: str | None = None  # default wss://api-feed.dhan.co if unset everywhere
DHAN_API_BASE_URL: str | None = None  # default https://api.dhan.co/v2

# Optional JSON string (same as env TRADING_AI_DHAN_LTP_MAP)
TRADING_AI_DHAN_LTP_MAP: str | None = None

# --- OpenAlgo (self-hosted; paper routing) ---
OPENALGO_BASE_URL: str | None = None  # e.g. "http://127.0.0.1:5000"
OPENALGO_API_KEY: str | None = None
OPENALGO_STRATEGY: str | None = None  # default TradingAIML

# --- Hugging Face Hub ---
HF_TOKEN: str | None = None
HUGGING_FACE_HUB_TOKEN: str | None = None
