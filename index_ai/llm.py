"""Optional LLM text generation for the advisory commentary layer.

Uses Google Gemini (free tier) if ``GEMINI_API_KEY`` is set, else Anthropic if
``ANTHROPIC_API_KEY`` is set. Returns ``None`` when neither is configured or the
call fails — every caller here is advisory and has a deterministic fallback, so a
missing key or a network blip never breaks the dashboard.

No SDK for Gemini — it is one httpx POST, and httpx is already a dependency.
"""

from __future__ import annotations

import os

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


def ask(system: str, prompt: str, *, max_tokens: int = 900) -> str | None:
    return _gemini(system, prompt, max_tokens) or _anthropic(system, prompt, max_tokens)


def _gemini(system: str, prompt: str, max_tokens: int) -> str | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    try:
        import httpx

        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        resp = httpx.post(
            _GEMINI_URL.format(model=model),
            params={"key": key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
            },
            timeout=30,
        )
        resp.raise_for_status()
        cands = resp.json().get("candidates") or []
        if not cands:
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip() or None
    except Exception:
        return None


def _anthropic(system: str, prompt: str, max_tokens: int) -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=os.getenv("AI_COMMENTARY_MODEL", "claude-sonnet-5"),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return (
            "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None
        )
    except Exception:
        return None


if __name__ == "__main__":  # self-check
    if enabled():
        out = ask("You are terse.", "Reply with the single word OK.", max_tokens=10)
        print(f"llm.py — provider responded: {out!r}")
    else:
        assert ask("s", "p") is None
        print("llm.py — no key set, ask() returns None (expected)")
