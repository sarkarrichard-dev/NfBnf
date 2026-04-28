from __future__ import annotations

import json
from typing import Any

from nbnf.india.market_clock import market_snapshot
from nbnf.ml.ingest import text_digest
from nbnf.quanttape.persona import QUANTTAPE_SYSTEM_INDIA
from nbnf.server import db


def build_briefing(*, use_llm: bool = True) -> dict[str, Any]:
    """
    Sitrep for the desk: IST session, watchlist, optional one-paragraph LLM voice.
    """
    snap = market_snapshot()
    wl = db.watchlist_list()
    ml_n = db.ml_datasets_count()
    digest_preview = text_digest(max_files=12, max_chars=1800) if ml_n else ""
    lines = [
        f"IST now: {snap['ist_iso']}",
        f"Session: {snap['phase']} - {snap['label']}",
        f"Watchlist ({len(wl)}): {', '.join(wl) if wl else '(empty - add symbols via desk)'}",
        (
            f"Local data catalog: {ml_n} profiled tabular files in SQLite. "
            "This is not model training; the brain only receives a capped digest."
        ),
    ]
    if digest_preview:
        lines.append("ML digest (sample):")
        lines.append(digest_preview)
    narrative: str | None = None
    if use_llm:
        try:
            from nbnf.llm_remote import chat

            payload: dict[str, Any] = {
                "india": snap,
                "watchlist": wl,
                "ml_dataset_count": ml_n,
            }
            if digest_preview:
                payload["ml_digest_preview"] = digest_preview
            narrative = chat(
                [
                    {"role": "system", "content": QUANTTAPE_SYSTEM_INDIA},
                    {
                        "role": "user",
                        "content": (
                            "Give a 4-sentence sitrep for the operator from this JSON. "
                            "If ml_digest_preview is present, briefly note how it relates to the watchlist "
                            "(descriptive only, no trade advice):\n"
                            + json.dumps(payload, default=str)
                        ),
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
        "ml_dataset_count": ml_n,
        "ml_digest_preview": digest_preview or None,
    }
