from __future__ import annotations

from typing import Any

import httpx

from nbnf.settings import get_settings


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> str:
    """
    Call a remote OpenAI-compatible chat completions API. Keys and base URL
    come from the environment (see .env.example).
    """
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; remote LLM calls are disabled.")

    url = f"{s.openai_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": s.openai_chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    with httpx.Client(timeout=120.0) as c:
        r = c.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()

    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected chat completions payload: {data!r}") from e
