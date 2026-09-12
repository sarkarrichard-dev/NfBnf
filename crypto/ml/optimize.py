"""Walk-forward parameter auto-tune for the crypto video strategies.

Richard (2026-09-08): the new strategies' parameters are tuned "through the
machine learning and AI", not left at the video defaults. Same discipline as
``crypto/ml/model.py``:

  * random search over a small per-parameter grid (not a full sweep),
  * score = net USD after ``crypto.charges`` on **rolling out-of-sample folds**
    (rule systems have no train step, so every fold is OOS),
  * a combo is eligible only if a majority of folds are non-negative,
  * the winner must survive a **neighbour-stability check** — nudging any one
    parameter ±1 grid step must not flip the score's sign. This is the
    ``strategy-findings.md`` "3-day hold optimum whose neighbours flipped" trap.

Output → ``memory/crypto_strategy_params.json``; ``crypto.lanes`` reads it via
``tuned_params(name)``.

Richard (2026-09-12): he does not trust this — it scores parameters against
*downloaded historical candles*, not real market behaviour, and he wants
"all testing on live data from the market" instead. So ``retune_all()`` no
longer runs automatically; it stays available as a manual/diagnostic tool
(``POST /api/crypto/ml/optimize``, or set ``CRYPTO_BACKTEST_AUTOTUNE=true``
to restore the nightly run) but its output is not treated as ground truth.
The trusted path going forward is ``crypto/ml/model.py`` (trains only on the
real live journal, gates only once it beats trading everything OOS) plus the
per-(strategy, instrument) Strategy P&L scorecard built from real trades.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto.config import CRYPTO_MEMORY, crypto_settings

logger = logging.getLogger(__name__)

_PARAMS_PATH = CRYPTO_MEMORY / "crypto_strategy_params.json"

FOLDS = 3
MAX_COMBOS = 24
MIN_TRADES = 20          # across all folds+symbols
MIN_POS_FOLD_FRAC = 0.5  # >= half the folds non-negative
TUNE_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD")
TUNE_DAYS = 90

# small, sensible grid per tunable — the video default is always in the list
SEARCH_SPACE: dict[str, dict[str, list]] = {
    "ema_jaguar": {
        "fast": [8, 13, 21],
        "slow": [34, 55, 89],
    },
    "bb_reversal": {
        "bb_len": [14, 20, 30],
        "bb_dev": [1.5, 2.0, 2.5],
        "swing_left": [2, 3, 5],
        "swing_right": [1, 2],
    },
    "vp_edge": {
        "vp_lookback": [64, 96, 128],
        "vp_bins": [20, 30, 40],
        "value_area_pct": [0.6, 0.7, 0.8],
        "edge_buffer_pct": [0.3, 0.6, 1.0],
    },
    "ak_roxx_pro": {
        "require_beyond_cpr": [True, False],
        "require_alpha2_agree": [False, True],
        "upper_len": [6, 8, 12],
        "lower_len": [6, 8, 12],
    },
}


# ---- params file -----------------------------------------------------------

def _load() -> dict[str, Any]:
    try:
        return json.loads(_PARAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tuned_params(name: str) -> dict[str, Any]:
    """The tuned params for ``name`` if a stable positive combo was found, else {}."""
    row = _load().get(name) or {}
    return dict(row.get("params") or {}) if row.get("stable") and row.get("net_usd", 0) > 0 else {}


def status() -> dict[str, Any]:
    blob = _load()
    return {
        k: {"params": v.get("params"), "net_usd": v.get("net_usd"),
            "stable": v.get("stable"), "tuned_at": v.get("tuned_at")}
        for k, v in blob.items()
    }


# ---- scoring --------------------------------------------------------------

_MIN_FOLD = 280  # >= _WINDOW (220) + room to score


def _folds(frame: pd.DataFrame, k: int) -> list[pd.DataFrame]:
    n = len(frame)
    k = min(k, max(1, n // _MIN_FOLD))
    if k <= 1:
        return [frame] if n >= _MIN_FOLD else []
    step = n // k
    return [frame.iloc[i * step : (i + 1) * step].reset_index(drop=True) for i in range(k)]


def _score(name: str, frames: dict[str, pd.DataFrame], s, combo: dict) -> dict[str, Any]:
    from crypto.backtest import backtest_simple

    fold_nets: list[float] = []
    n_trades = 0
    for sym, fr in frames.items():
        for fold in _folds(fr, FOLDS):
            trades = backtest_simple(name, sym, 0, s, cfg_overrides=combo, frame=fold)
            fold_nets.append(round(sum(t.pnl_usd for t in trades), 2))
            n_trades += len(trades)
    net = round(sum(fold_nets), 2)
    pos_frac = sum(1 for x in fold_nets if x >= 0) / len(fold_nets) if fold_nets else 0.0
    return {"net_usd": net, "fold_nets": fold_nets, "pos_fold_frac": round(pos_frac, 2),
            "n_trades": n_trades}


def _combos(space: dict[str, list], cap: int) -> list[dict]:
    keys = list(space)
    full: list[dict] = [{}]
    for k in keys:
        full = [{**c, k: v} for c in full for v in space[k]]
    if len(full) <= cap:
        return full
    rnd = random.Random(42)
    return rnd.sample(full, cap)


def _neighbours(space: dict[str, list], combo: dict) -> list[dict]:
    out = []
    for k, vals in space.items():
        i = vals.index(combo[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                out.append({**combo, k: vals[j]})
    return out


# ---- the tune -----------------------------------------------------------

def _fetch(symbols, tf: str, days: int) -> dict[str, pd.DataFrame]:
    from crypto.delta import market_data

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            fr = market_data.candles(sym, tf, days=days)
            if len(fr) >= 260:
                out[sym] = fr
        except Exception:
            logger.warning("optimize: candles(%s, %s) failed", sym, tf, exc_info=True)
    return out


def optimize_one(name: str, *, frames: dict[str, pd.DataFrame] | None = None,
                 days: int = TUNE_DAYS, symbols=TUNE_SYMBOLS,
                 max_combos: int = MAX_COMBOS) -> dict[str, Any]:
    from crypto.backtest import _SIMPLE

    if name not in SEARCH_SPACE:
        return {"tuned": False, "reason": f"no search space for {name}"}
    space = SEARCH_SPACE[name]
    s = crypto_settings()
    tf = _SIMPLE[name][1]
    tf = tf(s) if callable(tf) else tf  # ak_roxx_pro/ichimoku's timeframe is settings-dependent
    frames = frames if frames is not None else _fetch(symbols, tf, days)
    if not frames:
        return {"tuned": False, "reason": "no candle history"}

    scored = [(c, _score(name, frames, s, c)) for c in _combos(space, max_combos)]
    eligible = [
        (c, r) for c, r in scored
        if r["n_trades"] >= MIN_TRADES and r["pos_fold_frac"] >= MIN_POS_FOLD_FRAC
    ]
    result = {
        "tuned_at": datetime.now(timezone.utc).isoformat(),
        "days": days, "symbols": list(frames),
        "candidates": len(scored), "eligible": len(eligible),
    }
    if not eligible:
        best = max(scored, key=lambda cr: cr[1]["net_usd"], default=(None, {"net_usd": 0}))
        return {**result, "stable": False, "params": best[0] or {},
                "net_usd": best[1]["net_usd"], "reason": "no eligible combo"}

    combo, rep = max(eligible, key=lambda cr: cr[1]["net_usd"])
    if rep["net_usd"] <= 0:
        return {**result, "stable": False, "params": combo, "net_usd": rep["net_usd"],
                "reason": "best eligible combo is not positive"}

    # neighbour-stability: no single-step nudge may flip the sign
    flips = [
        n for n in _neighbours(space, combo)
        if _score(name, frames, s, n)["net_usd"] < 0
    ]
    stable = not flips
    return {**result, "stable": stable, "params": combo, "net_usd": rep["net_usd"],
            "fold_nets": rep["fold_nets"], "n_trades": rep["n_trades"],
            "unstable_neighbours": len(flips)}


def retune_all(*, days: int = TUNE_DAYS) -> dict[str, Any]:
    blob = _load()
    for name in SEARCH_SPACE:
        try:
            blob[name] = optimize_one(name, days=days, max_combos=MAX_COMBOS)
            logger.info("optimize %s: %s", name, blob[name].get("reason")
                        or f"net ${blob[name].get('net_usd')} stable={blob[name].get('stable')}")
        except Exception:
            logger.warning("optimize %s failed", name, exc_info=True)
    try:
        CRYPTO_MEMORY.mkdir(parents=True, exist_ok=True)
        _PARAMS_PATH.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("could not write %s", _PARAMS_PATH)
    return blob


if __name__ == "__main__":  # self-check — synthetic frames, no network
    import numpy as np

    def _ou(n, seed):
        rng = np.random.default_rng(seed)
        px = np.empty(n)
        px[0] = 100.0
        for i in range(1, n):
            px[i] = px[i - 1] + 0.1 * (100 - px[i - 1]) + rng.normal(0, 0.4)
        return pd.DataFrame({
            "datetime": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": px, "high": px + 0.3, "low": px - 0.3, "close": px,
            "volume": np.abs(rng.normal(10, 2, n)),
        })

    frames = {"BTCUSD": _ou(900, 1), "ETHUSD": _ou(900, 2)}
    # folds are contiguous and chronological
    fs = _folds(frames["BTCUSD"], 3)
    assert fs and fs[0]["datetime"].iloc[-1] < fs[-1]["datetime"].iloc[0]

    r = optimize_one("ema_jaguar", frames=frames, days=0, max_combos=3)
    assert "stable" in r and "params" in r and "eligible" in r
    # _neighbours produces valid ±1-step combos inside the grid
    nb = _neighbours(SEARCH_SPACE["ema_jaguar"], {"fast": 13, "slow": 34})
    assert all(c["fast"] in SEARCH_SPACE["ema_jaguar"]["fast"] for c in nb)
    print("crypto.ml.optimize self-check ok —", {k: r.get(k) for k in ("stable", "net_usd", "eligible")})
