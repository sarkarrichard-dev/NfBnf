"""
One market-context snapshot, assembled once per session at the 09:00 analysis start.

Pulls the four external inputs together and turns them into features the brain
can score, plus a small set of *hard* trading conditions that don't need a model
to justify:

  * FII index-futures positioning (end-of-day, prior session)
  * India VIX level / percentile, and the option-chain IV term structure
  * Intraday OI shift and where the writers' walls sit
  * Expiry-day pinning

Everything degrades gracefully: any source that fails is recorded as unavailable
and its features fall back to neutral, so a dead NSE endpoint can never stop the
session. Nothing here fires a trade — it conditions and explains.
"""

from __future__ import annotations

import json
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist
from index_ai.market_context import oi_flow, participant_oi, vix

SNAPSHOT_PATH = MEMORY_DIR / "market_context.json"


def build(*, refresh: bool = False) -> dict[str, Any]:
    """Assemble the day's context. Safe to call repeatedly."""
    session = now_ist().date().isoformat()
    ctx: dict[str, Any] = {
        "session": session,
        "built_at_ist": now_ist().isoformat(timespec="seconds"),
        "sources": {},
    }

    try:
        p = participant_oi.load(refresh=refresh)
        ctx["participant_oi"] = p
        ctx["sources"]["participant_oi"] = (
            f"NSE EOD as of {p['as_of']} ({p.get('stale_days', '?')}d old)" if p else "unavailable"
        )
    except Exception as exc:
        ctx["participant_oi"], ctx["sources"]["participant_oi"] = None, f"error: {exc}"

    try:
        v = vix.load(refresh=refresh)
        ctx["vix"] = v
        ctx["sources"]["vix"] = f"NSE India VIX {v['last']} ({v['regime']})" if v else "unavailable"
    except Exception as exc:
        ctx["vix"], ctx["sources"]["vix"] = None, f"error: {exc}"

    ctx["notes"] = _notes(ctx)
    ctx["conditions"] = conditions(ctx)
    _save(ctx)
    return ctx


def attach_chain_context(
    ctx: dict[str, Any],
    *,
    instrument: str,
    rows: dict[float, dict[str, Any]],
    spot: float,
    is_expiry_day: bool,
    near_atm_iv: float | None = None,
    next_atm_iv: float | None = None,
) -> dict[str, Any]:
    """Fold live-chain readings (pinning, OI shift, IV term) into the snapshot."""
    per = ctx.setdefault("per_instrument", {})
    entry: dict[str, Any] = {}
    try:
        entry["pinning"] = oi_flow.pinning(rows, spot, is_expiry_day=is_expiry_day)
    except Exception as exc:
        entry["pinning"] = {"error": str(exc)}
    try:
        entry["oi_shift"] = oi_flow.oi_shift(instrument)
    except Exception as exc:
        entry["oi_shift"] = {"error": str(exc)}
    entry["iv_term"] = vix.iv_term_structure(near_atm_iv, next_atm_iv)
    per[str(instrument).upper()] = entry
    ctx["conditions"] = conditions(ctx)
    _save(ctx)
    return ctx


def features(ctx: dict[str, Any] | None, instrument: str | None = None) -> dict[str, float]:
    """Flat feature vector for the brain. Neutral defaults when a source is missing."""
    c = ctx or {}
    per = (c.get("per_instrument") or {}).get(str(instrument or "").upper(), {})
    out: dict[str, float] = {}
    out.update(participant_oi.features(c.get("participant_oi")))
    out.update(vix.features(c.get("vix"), per.get("iv_term")))
    out.update(oi_flow.features(per.get("pinning"), per.get("oi_shift")))
    return out


def conditions(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hard, non-model trading conditions the context implies.

    These are mechanical, not learned: a stressed VIX with near-dated vol bid is a
    known bad state to be short premium in, regardless of what any model says.
    """
    v = ctx.get("vix") or {}
    regime = str(v.get("regime") or "")
    blocks: list[str] = []
    warn: list[str] = []

    if regime == "STRESSED":
        blocks.append(f"India VIX {v.get('last')} — stressed; premium selling is where sellers blow up")
    elif regime == "ELEVATED":
        warn.append(f"India VIX {v.get('last')} elevated — size down")

    for key, entry in (ctx.get("per_instrument") or {}).items():
        term = entry.get("iv_term") or {}
        if term.get("shape") == "BACKWARDATION":
            warn.append(f"{key}: near-expiry IV richer than next ({term.get('ratio')}) — event priced in")
        pin = entry.get("pinning") or {}
        if pin.get("pin_pressure"):
            warn.append(f"{key}: expiry-day pin near {pin.get('max_pain')} — directional moves get faded")

    return {
        "allow_selling": not blocks,
        "blocks": blocks,
        "warnings": warn,
    }


def _notes(ctx: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    p = ctx.get("participant_oi")
    if p:
        fii = int(p.get("fii_index_fut_net", 0))
        cli = int(p.get("client_index_fut_net", 0))
        side = "net LONG" if fii > 0 else "net SHORT"
        notes.append(
            f"FII {side} {abs(fii):,} index futures (as of {p['as_of']}); "
            f"client net {'long' if cli > 0 else 'short'} {abs(cli):,} — retail is usually the other side."
        )
    v = ctx.get("vix")
    if v:
        notes.append(
            f"India VIX {v['last']} ({v['regime']}, {v['percentile_1y']:.0%} of 1y range, "
            f"{v['change_pct']:+.1f}% today)."
        )
    return notes


def _save(ctx: dict[str, Any]) -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(ctx, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def latest() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_for_session(*, refresh: bool = False) -> dict[str, Any]:
    """Today's snapshot, building it if the cached one is from a previous session."""
    cur = latest()
    if not refresh and cur and cur.get("session") == now_ist().date().isoformat():
        return cur
    return build(refresh=refresh)


if __name__ == "__main__":  # ponytail self-check
    ctx = {
        "vix": {"last": 27.0, "regime": "STRESSED", "percentile_1y": 0.9, "change_pct": 12.0},
        "participant_oi": {"as_of": "2026-08-28", "stale_days": 1,
                           "fii_index_fut_net": -202633, "client_index_fut_net": 176959,
                           "fii_net_options_bias": -824380},
    }
    cond = conditions(ctx)
    assert cond["allow_selling"] is False and cond["blocks"]
    ctx["vix"]["regime"] = "CALM"
    assert conditions(ctx)["allow_selling"] is True

    ctx["per_instrument"] = {"NIFTY": {
        "iv_term": vix.iv_term_structure(20.0, 14.0),
        "pinning": {"pin_pressure": True, "max_pain": 24000, "is_expiry_day": True,
                    "max_pain_distance_pct": 0.1},
        "oi_shift": {"writer_bias": "CALL_WRITING"},
    }}
    c2 = conditions(ctx)
    assert any("BACKWARD" in w or "richer" in w for w in c2["warnings"])
    assert any("pin" in w for w in c2["warnings"])

    f = features(ctx, "NIFTY")
    assert f["vix_stressed"] == 0.0 and f["pin_pressure"] == 1.0
    assert f["oi_writer_bias"] == -1.0 and abs(f["fii_fut_net_lakh"] + 2.0263) < 1e-3
    empty = features(None)
    assert empty["vix_pctile"] == 0.5 and empty["participant_oi_stale"] == 1.0
    assert len(_notes(ctx)) == 2
    print("context.py self-check ok")
