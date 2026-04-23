from __future__ import annotations

import json
from typing import Any

from nbnf.india.market_clock import market_snapshot
from nbnf.quanttape.persona import QUANTTAPE_SYSTEM_INDIA
from nbnf.server import db


def build_briefing(*, use_llm: bool = True) -> dict[str, Any]:
    """
    Sitrep for the desk: IST session, watchlist, optional one-paragraph LLM voice.
    """
    snap = market_snapshot()
    wl = db.watchlist_list()
    lines = [
        f"IST now: {snap['ist_iso']}",
        f"Session: {snap['phase']} — {snap['label']}",
        f"Watchlist ({len(wl)}): {', '.join(wl) if wl else '(empty — add symbols via desk)'}",
    ]
    narrative: str | None = None
    if use_llm:
        try:
            from nbnf.llm_remote import chat

            payload = {"india": snap, "watchlist": wl}
            narrative = chat(
                [
                    {"role": "system", "content": QUANTTAPE_SYSTEM_INDIA},
                    {
                        "role": "user",
                        "content": "Give a 4-sentence sitrep for the operator from this JSON:\n"
                        + json.dumps(payload, default=str),
                    },
                ],
                temperature=0.2,
                max_tokens=350,
            ).strip()
        except Exception:
            narrative = None

    text = "\n".join(lines)
    if narrative:
        text = text + "\n\n" + narrative

    return {
        "type": "briefing",
        "india": snap,
        "watchlist": wl,
        "lines": lines,
        "narrative": narrative,
        "text": text,
    }
