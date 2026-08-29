"""
LLM commentary layer — explains what the statistical brain decided.

Strictly advisory. This module can read state and produce prose; it can never
gate an entry, size a position, or place an order. That separation is deliberate:
an LLM in the execution path adds a non-deterministic, un-backtestable failure
mode to a system whose whole value is measurable edge. The statistical brain
(``brain.model`` + ``brain.regime``) decides; this narrates and flags anomalies.

Enabled by ENABLE_AI_COMMENTARY with ANTHROPIC_API_KEY set. Without either it
returns a deterministic locally-composed summary — the dashboard always has
something to show and never depends on a network call.
"""

from __future__ import annotations

import json
import os
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist, now_ist_iso

CACHE_PATH = MEMORY_DIR / "ai_commentary.json"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 900

_SYSTEM = """You are the analyst for an Indian index options/futures trading system.
You are ADVISORY ONLY: never instruct the system to take, size, or skip a trade, and
never state a market prediction as fact. Your job is to explain what the system's own
statistics say and to flag things a careful trader would want to notice.

Ground every claim in the numbers you are given. If the data is thin or ambiguous, say
so plainly rather than manufacturing a narrative. Prefer "the gate is unarmed because
walk-forward showed it lost money" over "the model is cautious today". Be concise and
concrete. No hype, no emoji, no disclaimers beyond what the data warrants."""


def enabled() -> bool:
    return (
        os.getenv("ENABLE_AI_COMMENTARY", "false").strip().lower() in {"1", "true", "yes", "on"}
        and bool(os.getenv("ANTHROPIC_API_KEY"))
    )


def _snapshot() -> dict[str, Any]:
    """Everything the commentary is allowed to see. Numbers only, no credentials."""
    from index_ai.brain.gate import status as brain_status
    from index_ai.brain.store import build_dataset

    snap: dict[str, Any] = {"generated_at_ist": now_ist_iso(), "brain": brain_status()}
    try:
        ds = build_dataset(include_backtest=False)
        snap["dataset"] = {"rows": ds["total_rows"], "by_source": ds["counts"]}
    except Exception as exc:
        snap["dataset"] = {"error": str(exc)}
    for name, fn in (
        ("futures_paper", "index_ai.strategies.futures.paper:futures_paper_status"),
        ("options_cpr_paper", "index_ai.strategies.options_cpr.paper:options_cpr_paper_status"),
    ):
        try:
            mod_name, attr = fn.split(":")
            mod = __import__(mod_name, fromlist=[attr])
            st = getattr(mod, attr)()
            snap[name] = {k: st.get(k) for k in ("enabled", "today", "all_time", "open_positions")}
        except Exception as exc:
            snap[name] = {"error": str(exc)}
    try:
        from index_ai.scanner import scanner_status

        ss = scanner_status()
        snap["scanner"] = {
            "running": ss.get("running"), "cycles": ss.get("cycles"),
            "last_error": ss.get("last_error"),
            "tripped_stages": (ss.get("health") or {}).get("tripped"),
            "last_cycle_ms": (ss.get("health") or {}).get("last_cycle_ms"),
        }
    except Exception as exc:
        snap["scanner"] = {"error": str(exc)}
    return snap


def _local_summary(snap: dict[str, Any], kind: str) -> str:
    """Deterministic fallback — always available, never needs a network call."""
    b = snap.get("brain") or {}
    lines = [f"{'Pre-open brief' if kind == 'pre_open' else 'End-of-day review'} — {now_ist().strftime('%d %b %Y')}"]
    rows, live = b.get("rows", 0), b.get("live_rows", 0)
    if b.get("gate_armed"):
        lines.append(
            f"ML gate ARMED at P(win) >= {b.get('min_win_prob_gate'):.0%}, trained on {rows} trades "
            f"({live} live). Walk-forward edge: Rs {b.get('oos_delta_rupees'):,}."
        )
    else:
        d = b.get("oos_delta_rupees")
        why = f" (walk-forward delta Rs {d:,})" if isinstance(d, (int, float)) else ""
        lines.append(f"ML gate NOT armed{why} — every setup passes; only the regime filter is active.")
    for lane in ("futures_paper", "options_cpr_paper"):
        st = snap.get(lane) or {}
        if st.get("enabled"):
            today = st.get("today") or {}
            allt = st.get("all_time") or {}
            lines.append(
                f"{lane.replace('_', ' ')}: {today.get('closed', 0)} closed today "
                f"(Rs {today.get('net_rupees', 0):,.0f}), all-time Rs {allt.get('net_rupees', 0):,.0f} "
                f"over {allt.get('closed', 0)} trades."
            )
    sc = snap.get("scanner") or {}
    if sc.get("tripped_stages"):
        lines.append(f"Scanner breakers tripped: {', '.join(sc['tripped_stages'])}.")
    if sc.get("last_error"):
        lines.append(f"Last scanner error: {sc['last_error']}")
    return "\n".join(lines)


def _ask_claude(snap: dict[str, Any], kind: str) -> str | None:
    try:
        import anthropic
    except Exception:
        return None
    prompt = (
        f"Write a short {'pre-open brief' if kind == 'pre_open' else 'end-of-day review'} "
        "for the operator of this trading system. Cover: what the ML gate is doing and why, "
        "how each live lane is performing, and anything anomalous worth a human look "
        "(stalled scanner stages, drift, a lane losing consistently). "
        "Under 200 words, plain prose, no bullet-point padding.\n\n"
        f"SYSTEM STATE (JSON):\n{json.dumps(snap, indent=2, default=str)[:12000]}"
    )
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=os.getenv("AI_COMMENTARY_MODEL", DEFAULT_MODEL),
            max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None
    except Exception:
        return None


def generate(kind: str = "pre_open") -> dict[str, Any]:
    """Produce commentary. Always returns text — LLM if available, local otherwise."""
    snap = _snapshot()
    text, source = None, "local"
    if enabled():
        text = _ask_claude(snap, kind)
        source = "claude" if text else "local"
    if not text:
        text = _local_summary(snap, kind)
    out = {
        "kind": kind,
        "source": source,
        "advisory_only": True,
        "text": text,
        "snapshot": snap,
        "generated_at_ist": now_ist_iso(),
    }
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.is_file() else {}
        cache[kind] = out
        CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
    return out


def latest(kind: str = "pre_open") -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8")).get(kind)
    except Exception:
        return None


if __name__ == "__main__":  # ponytail self-check
    out = generate("eod")
    assert out["advisory_only"] is True
    assert out["text"] and "gate" in out["text"].lower()
    assert out["source"] in {"local", "claude"}
    assert "brain" in out["snapshot"]
    print(f"commentary.py self-check ok (source={out['source']})\n---\n{out['text']}")
