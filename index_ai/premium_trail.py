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
_CFG: dict[str, dict[str, float]] = {
    "NIFTY": {"hard_stop_pts": 11.0, "first_target_pct": 0.25, "trail_pts": 5.0},
    "BANKNIFTY": {"hard_stop_pts": 100.0, "first_target_pct": 0.25, "trail_pts": 35.0},
    # SENSEX is paused; keep sane values so nothing divides by zero if re-enabled.
    "SENSEX": {"hard_stop_pts": 100.0, "first_target_pct": 0.25, "trail_pts": 35.0},
}

_DEFAULT = {"hard_stop_pts": 25.0, "first_target_pct": 0.25, "trail_pts": 10.0}


def premium_trail_cfg(instrument_key: str) -> dict[str, float]:
    return _CFG.get(str(instrument_key or "").strip().upper(), _DEFAULT)


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
) -> tuple[dict[str, Any], bool, str | None]:
    """Returns (meta, should_exit, reason). Pure — safe to call every tick."""
    cfg = premium_trail_cfg(instrument_key)
    m = dict(meta)
    entry = float(m.get("pt_entry") or current_premium)
    if not m.get("pt_entry"):
        m["pt_entry"] = entry
    d = 1 if int(m.get("pt_dir") or -1) >= 0 else -1
    cur = float(current_premium)

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
    print("premium_trail.py self-check ok")
