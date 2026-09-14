"""OI-primary direction for the credit-sell lane.

Richard (2026-09-10) knows option selling and wants the sell lane driven by the
**OI profile** — the strikes where call/put writers have the most open interest
are the real ceiling and floor — plus **max pain** as a bias. CPR is kept only
for target / stop placement (classic pivots), not for picking direction.

    support    = the max-put-OI strike   (put writers defend the floor)
    resistance = the max-call-OI strike  (call writers defend the ceiling)

* **Bull put spread** (bullish credit) — spot is holding above the support wall
  with a buffer, and there is room up to the resistance wall. Sell the put
  spread under the floor.
* **Bear call spread** — mirrored under the ceiling.
* Spot wedged between the walls, walls crossed, a wall missing, or a max-pain
  conflict → **no OI read**; ``decide`` says so and the caller falls back to the
  CPR + EMA direction (`fallback_ok=True`).
* Spot pinned to max pain → **hard veto** (`fallback_ok=False`) — the day will
  chop, don't sell a directional credit however the CPR reads.

Pure. ``decide`` returns ``(action, reason, fallback_ok)``.
"""

from __future__ import annotations

from index_ai.options_oi import OptionOiContext, oi_walls

WALL_BUFFER_PCT = 0.15  # spot must be at least this far the safe side of the near wall
ROOM_MIN_PCT = 0.25  # …and at least this much clear space either side of mid to lean
MAX_PAIN_BIAS_PCT = 0.40  # |spot-maxpain| beyond this tilts the call/put choice
PIN_SKIP_PCT = 0.20  # spot within this of max pain → skip (chop)


def _pct_away(a: float, b: float) -> float:
    return abs(a - b) / b * 100.0 if b else 0.0


def decide(
    oi: OptionOiContext | None,
    price: float,
    *,
    max_pain: float | None = None,
) -> tuple[str | None, str, bool]:
    """``(action, reason, fallback_ok)``. ``action`` is one of the two spread
    strings or None. When None, ``fallback_ok`` is True if the caller should try
    the CPR read (the OI profile just has no direction), False if this is a hard
    veto (spot pinned to max pain)."""
    walls = oi_walls(oi)
    if walls is None:
        if (
            oi is not None
            and oi.max_put_oi_strike is not None
            and oi.max_call_oi_strike is not None
        ):
            put_w, call_w = float(oi.max_put_oi_strike), float(oi.max_call_oi_strike)
            return None, f"OI walls crossed (put {put_w:.0f} ≥ call {call_w:.0f})", True
        return None, "no OI walls — chain missing or flat", True
    support, resistance = walls

    if max_pain and _pct_away(price, max_pain) < PIN_SKIP_PCT:
        return None, f"spot pinned near max pain {max_pain:.0f} — no directional credit", False

    above_support = (price - support) / price * 100.0
    below_resistance = (resistance - price) / price * 100.0

    # spot sitting on a wall → the level is being tested; OI gives no clean read
    if above_support < WALL_BUFFER_PCT:
        return None, f"OI: spot at/through the put wall {support:.0f} — floor breaking", True
    if below_resistance < WALL_BUFFER_PCT:
        return None, f"OI: spot at/through the call wall {resistance:.0f} — ceiling breaking", True

    # dead-centre between the walls → no lean
    mid = (support + resistance) / 2.0
    centred = _pct_away(price, mid) < ROOM_MIN_PCT
    leaning_support = price < mid  # closer to the floor → sell the bull put spread under it

    # max-pain tilt — vetoes a fight against a strong pull, breaks a centred tie
    mp_dist = _pct_away(price, max_pain) if max_pain else 0.0
    mp_up = max_pain is not None and price < max_pain and mp_dist >= MAX_PAIN_BIAS_PCT
    mp_down = max_pain is not None and price > max_pain and mp_dist >= MAX_PAIN_BIAS_PCT

    if centred:
        if mp_up:
            return "SELL_BULL_PUT_SPREAD", f"OI: mid-range, max pain {max_pain:.0f} pulls up", True
        if mp_down:
            return (
                "SELL_BEAR_CALL_SPREAD",
                f"OI: mid-range, max pain {max_pain:.0f} pulls down",
                True,
            )
        return None, "OI: spot mid-range between walls, no max-pain tilt", True

    if leaning_support and not mp_down:
        return (
            "SELL_BULL_PUT_SPREAD",
            f"OI: spot {above_support:.2f}% above the put wall {support:.0f}, "
            f"nearer the floor than the call wall {resistance:.0f}"
            + (f", max pain {max_pain:.0f} pulls up" if mp_up else ""),
            True,
        )
    if not leaning_support and not mp_up:
        return (
            "SELL_BEAR_CALL_SPREAD",
            f"OI: spot {below_resistance:.2f}% below the call wall {resistance:.0f}, "
            f"nearer the ceiling than the put wall {support:.0f}"
            + (f", max pain {max_pain:.0f} pulls down" if mp_down else ""),
            True,
        )
    return None, f"OI: spot leaning one way, max pain {max_pain:.0f} pulls the other — skip", True


if __name__ == "__main__":  # self-check

    def ctx(pw, cw, pcr=1.0):
        return OptionOiContext(
            spot=0.0,
            atm_strike=0.0,
            total_call_oi=1,
            total_put_oi=1,
            pcr=pcr,
            max_call_oi_strike=cw,
            max_put_oi_strike=pw,
            bias="balanced",
            note="",
            confidence_adjustment=0.0,
        )

    a, _, fb = decide(ctx(23000, 24000), 23300)  # nearer the put wall → bull put spread
    assert a == "SELL_BULL_PUT_SPREAD", a
    a, _, fb = decide(ctx(23000, 24000), 23750)  # nearer the call wall → bear call spread
    assert a == "SELL_BEAR_CALL_SPREAD", a
    a, r, fb = decide(ctx(23000, 24000), 23030)  # sitting on the put wall → no read, fall back
    assert a is None and "floor breaking" in r and fb is True, (a, r, fb)
    a, r, fb = decide(ctx(24000, 23000), 23500)  # walls crossed → no read, fall back
    assert a is None and "crossed" in r and fb is True, (a, r, fb)
    a, r, fb = decide(ctx(23000, 24000), 23500, max_pain=23500)  # pinned → HARD veto
    assert a is None and "pinned" in r and fb is False, (a, r, fb)
    a, r, fb = decide(ctx(23000, 24000), 23500)  # dead centre, no tilt → no read, fall back
    assert a is None and "mid-range" in r and fb is True, (a, r, fb)
    a, _, fb = decide(ctx(23000, 24000), 23300, max_pain=24500)  # bull lean + max pain up → fine
    assert a == "SELL_BULL_PUT_SPREAD"
    a, _, fb = decide(ctx(23000, 24000), 23700, max_pain=22500)  # bear lean + max pain down → fine
    assert a == "SELL_BEAR_CALL_SPREAD"
    a, r, fb = decide(
        ctx(23000, 24000), 23300, max_pain=22000
    )  # bull lean, max pain DOWN → fall back
    assert a is None and "pulls the other" in r and fb is True, (a, r, fb)
    assert decide(None, 23500) == (None, "no OI walls — chain missing or flat", True)
    print("index_ai.strategies.oi_credit self-check ok")
