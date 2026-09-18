"""
The brain's win-probability model — one classifier over every lane.

Trained on ``brain.store.build_dataset`` (all journals, optionally seeded with
backtest rows). Validation is **chronological walk-forward**, never a random
split: a random split leaks future market regimes into training and reliably
produces a model that looks good and gates badly.

The gate only arms if the walk-forward pass shows it beats trading everything —
by out-of-sample *rupees*, not accuracy. A model that is 60% accurate but skips
the big winners is worse than no gate, and accuracy alone hides that.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np

from index_ai.brain.features import FEATURES, unified_features
from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist_iso

_log = logging.getLogger(__name__)

MODEL_DIR = MEMORY_DIR / "models"
MODEL_PATH = MODEL_DIR / "brain_model.joblib"
META_PATH = MODEL_DIR / "brain_meta.json"

SCHEMA_VERSION = 1
MIN_LIVE_ROWS = 40  # forward trades before a live-only model is allowed
MIN_ROWS = 60  # total rows (live + seed) before training at all
MIN_CLASS_ROWS = 12
WALK_BLOCKS = 5
DEFAULT_GATE = 0.50

# A feature added after the model was already trading needs its own minimum
# real (non-zero) support before it's allowed to influence training — found
# in a 2026-09-18 safety review: the option-Greeks columns below would
# otherwise fit a coefficient off literally 3 real rows the first time the
# unattended nightly EOD retrain ran, with no real out-of-sample evidence
# behind it (the chronological walk-forward split barely exercises a column
# that's only non-zero in the most recent few rows). Same "not enough data
# yet" discipline as MIN_LIVE_ROWS/MIN_CLASS_ROWS above, just per-column.
_YOUNG_FEATURE_MIN_SUPPORT: dict[str, int] = {
    "option_delta": 15,
    "option_gamma": 15,
    "option_theta_drag_pct": 15,
    "option_iv": 15,
    "dist_max_pain_pct": 15,
}


def _suppress_immature_features(X: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Zero out any young feature's column until it has real support —
    inert (exactly like it never existed) rather than fitting a coefficient
    off a handful of rows. Returns the (possibly modified) X and the names
    still suppressed, for the training meta."""
    X = X.copy()
    suppressed = []
    for name, min_support in _YOUNG_FEATURE_MIN_SUPPORT.items():
        if name not in FEATURES:
            continue
        idx = FEATURES.index(name)
        if int(np.count_nonzero(X[:, idx])) < min_support:
            X[:, idx] = 0.0
            suppressed.append(name)
    return X, suppressed


def _sklearn():
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return LogisticRegression, make_pipeline, StandardScaler
    except Exception:
        return None


def _new_model():
    LogisticRegression, make_pipeline, StandardScaler = _sklearn()
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
    )


def _pnl(meta: list[dict[str, Any]]) -> np.ndarray:
    return np.array([float(m.get("net_rupees") or 0.0) for m in meta], dtype=float)


def _best_gate(proba: np.ndarray, pnl: np.ndarray) -> tuple[float, float]:
    """Threshold maximising kept-trade rupees on the *training* slice."""
    best_t, best_v = 0.0, float(pnl.sum())
    for t in np.linspace(0.30, 0.75, 19):
        keep = proba >= t
        if keep.sum() < max(5, len(pnl) // 10):
            continue
        v = float(pnl[keep].sum())
        if v > best_v:
            best_t, best_v = float(t), v
    return best_t, best_v


def walk_forward(
    X: np.ndarray, y: np.ndarray, meta: list[dict[str, Any]], *, blocks: int = WALK_BLOCKS
) -> dict[str, Any]:
    """Chronological rolling validation. Returns OOS rupees with and without the gate."""
    pnl = _pnl(meta)
    n = len(y)
    start = n // 2
    step = max(1, (n - start) // blocks)
    oos_static = oos_gated = 0.0
    kept = total = 0
    gates: list[float] = []
    i = start
    while i < n:
        tr, te = slice(0, i), slice(i, min(i + step, n))
        if len(set(y[tr].tolist())) < 2:
            oos_static += float(pnl[te].sum())
            oos_gated += float(pnl[te].sum())
            i += step
            continue
        m = _new_model()
        m.fit(X[tr], y[tr])
        g, _ = _best_gate(m.predict_proba(X[tr])[:, 1], pnl[tr])
        gates.append(round(g, 3))
        p = m.predict_proba(X[te])[:, 1]
        keep = p >= g
        oos_static += float(pnl[te].sum())
        oos_gated += float(pnl[te].sum(where=keep, initial=0.0)) if keep.any() else 0.0
        kept += int(keep.sum())
        total += len(p)
        i += step
    return {
        "oos_static_rupees": round(oos_static),
        "oos_gated_rupees": round(oos_gated),
        "oos_delta_rupees": round(oos_gated - oos_static),
        "kept_fraction": round(kept / total, 3) if total else 0.0,
        "gates_by_block": gates,
        "suggested_gate": round(float(np.median(gates)), 3) if gates else DEFAULT_GATE,
    }


def train(*, include_backtest: bool | None = None, force: bool = False) -> dict[str, Any]:
    """Fit, validate walk-forward, and persist. Arms the gate only if it earns it."""
    from index_ai.brain.store import build_dataset

    if _sklearn() is None:
        return {"trained": False, "reason": "scikit-learn not installed"}

    live = build_dataset(include_backtest=False)
    seed = include_backtest if include_backtest is not None else live["live_rows"] < MIN_LIVE_ROWS
    ds = build_dataset(include_backtest=True) if seed else live

    n = ds["total_rows"]
    if n < MIN_ROWS and not force:
        return {
            "trained": False,
            "reason": f"{n} rows < {MIN_ROWS} minimum",
            "live_rows": live["live_rows"],
            "counts": ds["counts"],
        }
    X = np.array(ds["X"], dtype=float)
    X, suppressed_features = _suppress_immature_features(X)
    y = np.array(ds["y"], dtype=int)
    if min(int((y == 0).sum()), int((y == 1).sum())) < MIN_CLASS_ROWS and not force:
        return {
            "trained": False,
            "reason": "not enough of both win and loss outcomes",
            "live_rows": live["live_rows"],
        }

    wf = walk_forward(X, y, ds["meta"])
    model = _new_model()
    model.fit(X, y)

    # The gate arms only if walk-forward says it made money out-of-sample.
    armed = wf["oos_delta_rupees"] > 0
    gate = wf["suggested_gate"] if armed else 0.0

    coefs = {}
    try:
        lr = model[-1]
        coefs = {FEATURES[i]: round(float(lr.coef_[0][i]), 4) for i in range(len(FEATURES))}
    except Exception:
        pass

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import joblib

        joblib.dump(model, MODEL_PATH)
    except Exception as exc:
        return {"trained": False, "reason": f"could not persist model: {exc}"}

    meta = {
        "schema_version": SCHEMA_VERSION,
        "trained_at_ist": now_ist_iso(),
        "rows": n,
        "live_rows": live["live_rows"],
        "seeded_with_backtest": bool(seed),
        "counts": ds["counts"],
        "win_rate_pct": round(100 * float(y.mean()), 1),
        "walk_forward": wf,
        "gate_armed": armed,
        "min_win_prob_gate": gate,
        "feature_names": list(FEATURES),
        "coefficients": coefs,
        "suppressed_features": suppressed_features,
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"trained": True, **meta}


def load_meta() -> dict[str, Any]:
    if META_PATH.is_file():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"gate_armed": False, "min_win_prob_gate": 0.0, "rows": 0, "live_rows": 0}


def _load_model():
    if not MODEL_PATH.is_file():
        return None
    try:
        import joblib

        return joblib.load(MODEL_PATH)
    except Exception:
        return None


def score(trade_like: dict[str, Any]) -> dict[str, Any]:
    """Win probability + pass/fail for a proposed trade (same shape as a journal row).

    Fails open: with no armed model every setup passes, so a missing or unproven
    model never silently blocks the whole system.
    """
    meta = load_meta()
    gate = float(meta.get("min_win_prob_gate") or 0.0)
    armed = bool(meta.get("gate_armed"))
    feats = unified_features(trade_like)
    if feats is None or not armed:
        return {
            "win_probability": None,
            "passes": True,
            "gate": gate,
            "armed": armed,
            "reason": "gate not armed" if not armed else "unmappable setup",
        }
    model = _load_model()
    if model is None:
        return {
            "win_probability": None,
            "passes": True,
            "gate": gate,
            "armed": False,
            "reason": "model file missing",
        }
    try:
        x = np.array([[feats[k] for k in FEATURES]], dtype=float)
        p = float(model.predict_proba(x)[0][1])
    except Exception as exc:
        return {
            "win_probability": None,
            "passes": True,
            "gate": gate,
            "armed": armed,
            "reason": f"scoring failed: {exc}",
        }
    return {
        "win_probability": round(p, 4),
        "passes": p >= gate,
        "gate": gate,
        "armed": True,
        "reason": f"P(win)={p:.0%} vs gate {gate:.0%}",
    }


def status() -> dict[str, Any]:
    m = load_meta()
    wf = m.get("walk_forward") or {}
    return {
        "trained_at_ist": m.get("trained_at_ist"),
        "rows": m.get("rows", 0),
        "live_rows": m.get("live_rows", 0),
        "seeded_with_backtest": m.get("seeded_with_backtest", False),
        "gate_armed": m.get("gate_armed", False),
        "min_win_prob_gate": m.get("min_win_prob_gate", 0.0),
        "oos_delta_rupees": wf.get("oos_delta_rupees"),
        "oos_static_rupees": wf.get("oos_static_rupees"),
        "oos_gated_rupees": wf.get("oos_gated_rupees"),
        "kept_fraction": wf.get("kept_fraction"),
        "top_features": sorted((m.get("coefficients") or {}).items(), key=lambda kv: -abs(kv[1]))[
            :6
        ],
        "model_present": MODEL_PATH.is_file(),
    }
