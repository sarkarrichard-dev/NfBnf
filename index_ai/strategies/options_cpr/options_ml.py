"""
Walk-forward ML optimisation for the CPR+EMA option lanes (buy + directional sell).

Two knobs, both honest about lookahead:

  * ``walk_forward_gate`` — roll a train window over the closed-trade history, fit a
    logistic win/loss classifier on the entry ``features`` each trade carries, pick
    the probability threshold that maximises *training* net, then apply it to the
    next out-of-sample block. Reports static vs. gated vs. walk-forward-gated net.

  * ``walk_forward_params`` — same rolling split over a small parameter grid: for
    each window pick the grid point with the best training net, score it OOS.

ML cannot invent edge that is not in the signal — this measures whether trimming
the worst entries (or retuning monthly) turns a negative expectancy less negative.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

import numpy as np

_FEATURES: tuple[str, ...] = (
    "minute_of_day", "weekday", "cpr_width_pct", "dist_tc_pct", "dist_bc_pct",
    "ema_spread_pct", "atr_pct", "prev_day_range_pct", "ret_15m_pct", "vol_ratio",
)


def _matrix(trades: Sequence[dict]) -> np.ndarray:
    return np.array(
        [[float(t.get("features", {}).get(f, 0.0)) for f in _FEATURES] for t in trades],
        dtype=float,
    )


def _won(trades: Sequence[dict]) -> np.ndarray:
    return np.array([1 if float(t["net_rupees"]) > 0 else 0 for t in trades], dtype=int)


def _net(trades: Sequence[dict]) -> float:
    return float(sum(float(t["net_rupees"]) for t in trades))


def _fit(train: Sequence[dict]):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = _won(train)
    if len(set(y.tolist())) < 2:
        return None
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=500, class_weight="balanced", random_state=42),
    )
    model.fit(_matrix(train), y)
    return model


def _best_threshold(model, train: Sequence[dict]) -> float:
    """Threshold on P(win) that maximises training net (kept trades only)."""
    proba = model.predict_proba(_matrix(train))[:, 1]
    best_t, best_net = 0.0, _net(train)
    for t in np.linspace(0.3, 0.75, 19):
        kept = [tr for tr, p in zip(train, proba) if p >= t]
        if len(kept) < max(5, len(train) // 10):
            continue
        n = _net(kept)
        if n > best_net:
            best_t, best_net = float(t), n
    return best_t


def walk_forward_gate(
    trades: list[dict], *, train_frac: float = 0.5, blocks: int = 6
) -> dict[str, Any]:
    """Roll a train window; gate the next OOS block by the training-optimal threshold."""
    ts = sorted(trades, key=lambda t: str(t.get("session") or t.get("entry_time")))
    n = len(ts)
    if n < 40:
        return {"error": f"only {n} trades — need >= 40 for walk-forward", "static_net": round(_net(ts))}

    start = int(n * train_frac)
    block = max(1, (n - start) // blocks)
    kept_all: list[dict] = list(ts[:start])
    oos_static = oos_gated = 0.0
    thresholds: list[float] = []

    i = start
    while i < n:
        train = ts[:i]
        test = ts[i : i + block]
        model = _fit(train)
        if model is None:
            kept_all += test
            oos_static += _net(test)
            oos_gated += _net(test)
            i += block
            continue
        thr = _best_threshold(model, train)
        thresholds.append(round(thr, 3))
        proba = model.predict_proba(_matrix(test))[:, 1]
        kept = [tr for tr, p in zip(test, proba) if p >= thr]
        kept_all += kept
        oos_static += _net(test)
        oos_gated += _net(kept)
        i += block

    return {
        "trades": n,
        "static_net": round(_net(ts)),
        "oos_static_net": round(oos_static),
        "oos_gated_net": round(oos_gated),
        "oos_delta": round(oos_gated - oos_static),
        "gated_trade_count": len(kept_all),
        "thresholds_by_block": thresholds,
    }


def walk_forward_params(
    run_window: Callable[[dict[str, Any], int, int], list[dict]],
    *,
    n_days: int,
    grid: list[dict[str, Any]],
    train_days: int = 120,
    test_days: int = 21,
) -> dict[str, Any]:
    """``run_window(overrides, day_from, day_to)`` -> trades for that day-index slice.

    Rolls [train_days -> test_days]; each window keeps the grid point with the best
    training net and scores it out-of-sample.
    """
    oos_best = oos_static = 0.0
    picks: list[dict[str, Any]] = []
    start = 1
    while start + train_days + test_days <= n_days:
        tr_a, tr_b = start, start + train_days
        te_a, te_b = tr_b, tr_b + test_days
        scored = [(_net(run_window(g, tr_a, tr_b)), g) for g in grid]
        best_net, best_g = max(scored, key=lambda x: x[0])
        static_g = grid[0]
        oos_best += _net(run_window(best_g, te_a, te_b))
        oos_static += _net(run_window(static_g, te_a, te_b))
        picks.append({"window": [te_a, te_b], "params": best_g, "train_net": round(best_net)})
        start += test_days
    return {
        "windows": len(picks),
        "oos_static_net": round(oos_static),
        "oos_retuned_net": round(oos_best),
        "oos_delta": round(oos_best - oos_static),
        "picks": picks,
    }


if __name__ == "__main__":  # ponytail self-check
    rng = np.random.default_rng(0)
    fake = []
    for k in range(200):
        good = k % 3 == 0
        fake.append({
            "session": f"2026-01-{k % 28 + 1:02d}",
            "net_rupees": (rng.normal(300, 200) if good else rng.normal(-250, 200)),
            "features": {f: (2.0 if good else -2.0) + rng.normal(0, 0.5) for f in _FEATURES},
        })
    out = walk_forward_gate(fake)
    assert out["oos_gated_net"] >= out["oos_static_net"] - 1, out
    print("options_ml.py self-check ok:", {k: out[k] for k in ("static_net", "oos_static_net", "oos_gated_net")})
