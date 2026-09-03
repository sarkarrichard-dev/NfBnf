"""Per-index exit rule measured on the option's own price (premium points).

Richard's spec (2026-08-31), applied to both lanes on NIFTY and BANKNIFTY:

  * Flat hard stop until the first target is hit.
  * First target = a quarter of the entry premium moved in your favour.
  * After the target, a trailing stop follows the best premium seen and exits on
    a fixed bounce off it. The hard stop no longer applies once the trail arms —
    by then the position is in profit and the trail is always the tighter line.

"Premium" is the price of the single option you care about: the long option for
a naked buy, the *short* leg for a credit spread (the hedge just rides along and
is closed with it). Points, not rupees — the qty multiplier is the same either
way so the ratios hold across lot sizes.
"""

from __future__ import annotations

from typing import Any

# hard_stop / trail are premium points; target is a fraction of the entry price.
# Only indices with measured params belong here — anything absent keeps the
# legacy rupee %-of-max exit logic (see premium_trail_enabled).
_CFG: dict[str, dict[str, float]] = {
    "NIFTY": {"hard_stop_pts": 11.0, "first_target_pct": 0.25, "trail_pts": 5.0},
    "BANKNIFTY": {"hard_stop_pts": 100.0, "first_target_pct": 0.25, "trail_pts": 35.0},
}


def premium_trail_cfg(instrument_key: str) -> dict[str, float]:
    return _CFG[str(instrument_key or "").strip().upper()]


def premium_trail_enabled(instrument_key: str) -> bool:
    return str(instrument_key or "").strip().upper() in _CFG


def init_premium_trail(*, entry_premium: float, direction: int) -> dict[str, Any]:
    """direction: -1 = short (profit as premium falls), +1 = long (profit as it rises)."""
    p = float(entry_premium)
    return {
        "pt_entry": p,
        "pt_dir": 1 if int(direction) >= 0 else -1,
        "pt_best": p,
        "pt_target_hit": False,
    }


def update_premium_trail(
    meta: dict[str, Any],
    current_premium: float,
    instrument_key: str,
    *,
    index_price: float | None = None,
    pivot_target: float | None = None,
) -> tuple[dict[str, Any], bool, str | None]:
    """Returns (meta, should_exit, reason). Pure — safe to call every tick.

    ``pivot_target`` (a spot level in the trade's favour, e.g. R1 for a bull
    position) arms the trail early: once the index reaches it *and* the position
    is at least a trail-distance in profit, the flat hard stop gives way to
    trailing the best premium. It never closes the trade and never tightens to
    breakeven — it only brings the trail forward. The quarter-premium target
    arms it independently; whichever lands first wins.
    """
    cfg = premium_trail_cfg(instrument_key)
    m = dict(meta)
    entry = float(m["pt_entry"])  # init_premium_trail always sets this
    d = 1 if int(m.get("pt_dir") or -1) >= 0 else -1
    cur = float(current_premium)

    # Ignore an implausible print (zero / stale / fat-finger): one bad tick would
    # otherwise latch pt_target_hit or poison pt_best. Wait for a sane quote.
    if cur <= 0 or cur > 3.0 * entry:
        return m, False, None

    if d < 0:  # short leg — favourable is a lower premium
        favour = entry - cur
        adverse = cur - entry
        m["pt_best"] = min(float(m.get("pt_best", entry)), cur)
        bounce = cur - float(m["pt_best"])
        moved = "rose"
    else:  # long option — favourable is a higher premium
        favour = cur - entry
        adverse = entry - cur
        m["pt_best"] = max(float(m.get("pt_best", entry)), cur)
        bounce = float(m["pt_best"]) - cur
        moved = "fell"

    target_pts = float(cfg["first_target_pct"]) * entry
    hard = float(cfg["hard_stop_pts"])
    trail = float(cfg["trail_pts"])

    # Pivot-target early arm. Requires (a) a sane index quote, (b) the index has
    # actually reached the favourable pivot, and (c) the premium is already at
    # least a trail-distance in profit — so switching to the trail can only lock
    # in a gain, never book a loss on quote noise the hard stop would have sat
    # through. The 0.5–1.5× entry-index band rejects a zero / stale index tick.
    if (
        not m.get("pt_target_hit")
        and favour >= trail
        and pivot_target is not None
        and index_price is not None
        and m.get("entry_index_price")
    ):
        entry_idx = float(m["entry_index_price"])
        idx = float(index_price)
        need = abs(float(pivot_target) - entry_idx)
        toward = 1.0 if float(pivot_target) >= entry_idx else -1.0
        if (
            0.5 * entry_idx < idx < 1.5 * entry_idx
            and need > 0
            and (idx - entry_idx) * toward >= need
        ):
            m["pt_target_hit"] = True
            m["pt_armed_by"] = "pivot_target"

    if not m.get("pt_target_hit"):
        if favour >= target_pts:
            m["pt_target_hit"] = True
        elif adverse >= hard:
            return (
                m,
                True,
                (
                    f"Hard stop: premium moved {adverse:.1f} pts against entry {entry:.1f} "
                    f"(≥ {hard:g})."
                ),
            )
        else:
            return m, False, None

    # target hit → trailing only
    if bounce >= trail:
        locked = favour
        pct = locked / entry * 100 if entry else 0.0
        return (
            m,
            True,
            (
                f"Trailing exit: premium {moved} {bounce:.1f} pts off best "
                f"{float(m['pt_best']):.1f} — locked {locked:.1f} pts ({pct:.0f}%)."
            ),
        )
    return m, False, None


if __name__ == "__main__":  # spec walkthrough: BANKNIFTY short sold at 300
    m = init_premium_trail(entry_premium=300, direction=-1)
    m, ex, _ = update_premium_trail(m, 380, "BANKNIFTY")  # -80, not yet hard stop
    assert not ex
    m, ex, r = update_premium_trail(m, 405, "BANKNIFTY")  # -105 → hard stop
    assert ex and "Hard stop" in r

    m = init_premium_trail(entry_premium=300, direction=-1)
    m, ex, _ = update_premium_trail(m, 225, "BANKNIFTY")  # quarter target hit
    assert m["pt_target_hit"] and not ex
    m, ex, _ = update_premium_trail(m, 150, "BANKNIFTY")  # best now 150
    assert not ex
    m, ex, r = update_premium_trail(m, 185, "BANKNIFTY")  # +35 bounce → exit
    assert ex and "Trailing exit" in r and "locked 115" in r

    # long option, NIFTY bought at 60
    lm = init_premium_trail(entry_premium=60, direction=1)
    lm, ex, r = update_premium_trail(lm, 48, "NIFTY")  # -12 → hard stop (11)
    assert ex and "Hard stop" in r
    lm = init_premium_trail(entry_premium=60, direction=1)
    lm, ex, _ = update_premium_trail(lm, 75, "NIFTY")  # +15 = quarter of 60 → armed
    assert lm["pt_target_hit"]
    lm, ex, _ = update_premium_trail(lm, 90, "NIFTY")
    lm, ex, r = update_premium_trail(lm, 84, "NIFTY")  # 6 pt bounce ≥ 5 → exit
    assert ex and "Trailing exit" in r

    # pivot_target arms the trail early — but only once the short is already a
    # trail-distance (35 BANKNIFTY) in profit, so it can only lock a gain.
    pm = init_premium_trail(entry_premium=300, direction=-1)
    pm["entry_index_price"] = 52000.0
    # index at the pivot but premium only 15 pts in profit → NOT armed yet
    pm, ex, _ = update_premium_trail(pm, 285, "BANKNIFTY", index_price=52240, pivot_target=52200)
    assert not ex and not pm["pt_target_hit"]
    # premium now 45 pts in profit and index past pivot → armed
    pm, ex, _ = update_premium_trail(pm, 255, "BANKNIFTY", index_price=52240, pivot_target=52200)
    assert not ex and pm["pt_target_hit"] and pm["pt_armed_by"] == "pivot_target"
    pm, ex, r = update_premium_trail(pm, 291, "BANKNIFTY", index_price=52240, pivot_target=52200)
    assert ex and "Trailing exit" in r and "locked 9" in r  # 36-pt bounce, still +9

    # a zero / stale index tick must never arm it, even with the premium in profit
    bad = init_premium_trail(entry_premium=300, direction=-1)
    bad["entry_index_price"] = 52000.0
    bad, ex, _ = update_premium_trail(bad, 255, "BANKNIFTY", index_price=0.0, pivot_target=51800)
    assert not bad["pt_target_hit"]

    # absent a pivot_target, behaviour is unchanged
    nm = init_premium_trail(entry_premium=300, direction=-1)
    nm["entry_index_price"] = 52000.0
    nm, ex, _ = update_premium_trail(nm, 285, "BANKNIFTY", index_price=52240)
    assert not ex and not nm["pt_target_hit"]
    print("premium_trail.py self-check ok")
