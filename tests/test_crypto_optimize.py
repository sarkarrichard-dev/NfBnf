"""crypto/ml/optimize.py — walk-forward folds, the stability gate, the params file."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto.ml import optimize as opt


def _ou(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    px = np.empty(n)
    px[0] = 100.0
    for i in range(1, n):
        px[i] = px[i - 1] + 0.1 * (100 - px[i - 1]) + rng.normal(0, 0.4)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": px,
            "high": px + 0.3,
            "low": px - 0.3,
            "close": px,
            "volume": np.abs(rng.normal(10, 2, n)),
        }
    )


def test_folds_are_contiguous_and_chronological():
    fr = _ou(3000, 1)
    folds = opt._folds(fr, 3)
    assert len(folds) == 3
    for a, b in zip(folds, folds[1:]):
        assert a["datetime"].iloc[-1] < b["datetime"].iloc[0]
    assert sum(len(f) for f in folds) <= len(fr)
    # a short frame collapses to one fold, or none
    assert len(opt._folds(_ou(200, 1), 3)) == 0


def test_neighbours_stay_inside_the_grid():
    space = opt.SEARCH_SPACE["ema_jaguar"]
    nb = opt._neighbours(space, {"fast": 13, "slow": 34})
    assert nb and all(c["fast"] in space["fast"] and c["slow"] in space["slow"] for c in nb)
    # an edge value has only one neighbour per dimension
    nb_edge = opt._neighbours(space, {"fast": 8, "slow": 89})
    assert len(nb_edge) == 2


def test_optimize_one_reports_shape_and_never_claims_unstable_is_stable(monkeypatch):
    frames = {"BTCUSD": _ou(1500, 1), "ETHUSD": _ou(1500, 2)}
    r = opt.optimize_one("ema_jaguar", frames=frames, days=0, max_combos=4)
    assert {"stable", "params", "eligible", "candidates"} <= set(r)
    if r["stable"]:
        assert r["net_usd"] > 0  # stable is only ever set on a positive winner


def test_tuned_params_only_returns_stable_positive(tmp_path, monkeypatch):
    p = tmp_path / "params.json"
    monkeypatch.setattr(opt, "_PARAMS_PATH", p)
    import json

    p.write_text(
        json.dumps(
            {
                "ema_jaguar": {"params": {"fast": 21, "slow": 55}, "net_usd": 40.0, "stable": True},
                "bb_reversal": {"params": {"bb_len": 30}, "net_usd": 12.0, "stable": False},
                "vp_edge": {"params": {"vp_bins": 40}, "net_usd": -5.0, "stable": True},
            }
        ),
        encoding="utf-8",
    )
    assert opt.tuned_params("ema_jaguar") == {"fast": 21, "slow": 55}
    assert opt.tuned_params("bb_reversal") == {}  # not stable
    assert opt.tuned_params("vp_edge") == {}  # stable but negative
    assert opt.tuned_params("missing") == {}
