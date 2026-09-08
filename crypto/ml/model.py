"""The crypto win-probability model — one classifier over both crypto lanes.

Mirrors ``index_ai.brain.model`` deliberately (chronological walk-forward, a gate
that arms only if it beats trading everything out-of-sample in *USD*), but is a
separate stack: separate journal, separate model files, no shared code.

Fails open: with no armed model every setup passes, so an unproven model can
never silently block the lane.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from crypto.config import CRYPTO_MEMORY
from crypto.ml.dataset import build_dataset
from crypto.ml.features import FEATURES

_log = logging.getLogger(__name__)

MODEL_DIR = CRYPTO_MEMORY / "models"
MODEL_PATH = MODEL_DIR / "crypto_model.joblib"
META_PATH = MODEL_DIR / "crypto_meta.json"

MIN_LIVE_ROWS = 40
MIN_ROWS = 60
MIN_CLASS_ROWS = 12
WALK_BLOCKS = 5
DEFAULT_GATE = 0.50


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


def _pnl(meta: list[dict[str, Any]]):
    import numpy as np

    return np.array([float(m.get("pnl_usd") or 0.0) for m in meta], dtype=float)


def _best_gate(proba, pnl) -> tuple[float, float]:
    import numpy as np

    best_t, best_v = 0.0, float(pnl.sum())
    for t in np.linspace(0.30, 0.75, 19):
        keep = proba >= t
        if keep.sum() < max(5, len(pnl) // 10):
            continue
        v = float(pnl[keep].sum())
        if v > best_v:
            best_t, best_v = float(t), v
    return best_t, best_v


def walk_forward(X, y, meta, *, blocks: int = WALK_BLOCKS) -> dict[str, Any]:
    import numpy as np

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
        "oos_static_usd": round(oos_static, 2),
        "oos_gated_usd": round(oos_gated, 2),
        "oos_delta_usd": round(oos_gated - oos_static, 2),
        "kept_fraction": round(kept / total, 3) if total else 0.0,
        "suggested_gate": round(float(np.median(gates)), 3) if gates else DEFAULT_GATE,
    }


def train(*, force: bool = False) -> dict[str, Any]:
    if _sklearn() is None:
        return {"trained": False, "reason": "scikit-learn not installed"}
    import numpy as np

    ds = build_dataset()
    n = ds["total_rows"]
    if n < MIN_ROWS and not force:
        return {"trained": False, "reason": f"{n} rows < {MIN_ROWS} minimum",
                "live_rows": ds["live_rows"]}
    X = np.array(ds["X"], dtype=float)
    y = np.array(ds["y"], dtype=int)
    if min(int((y == 0).sum()), int((y == 1).sum())) < MIN_CLASS_ROWS and not force:
        return {"trained": False, "reason": "not enough of both win and loss outcomes",
                "live_rows": ds["live_rows"]}

    wf = walk_forward(X, y, ds["meta"])
    model = _new_model()
    model.fit(X, y)

    armed = wf["oos_delta_usd"] > 0 and ds["live_rows"] >= MIN_LIVE_ROWS
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
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows": n,
        "live_rows": ds["live_rows"],
        "win_rate_pct": round(100 * float(y.mean()), 1),
        "walk_forward": wf,
        "gate_armed": armed,
        "min_win_prob_gate": gate,
        "feature_names": list(FEATURES),
        "coefficients": coefs,
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
    """Win probability + pass/fail. Fails open."""
    from crypto.ml.features import row_features

    meta = load_meta()
    gate = float(meta.get("min_win_prob_gate") or 0.0)
    armed = bool(meta.get("gate_armed"))
    feats = row_features(trade_like)
    if feats is None or not armed:
        return {"win_probability": None, "passes": True, "gate": gate, "armed": armed,
                "reason": "gate not armed" if not armed else "unmappable setup"}
    model = _load_model()
    if model is None:
        return {"win_probability": None, "passes": True, "gate": gate, "armed": False,
                "reason": "model file missing"}
    try:
        import numpy as np

        x = np.array([[feats[k] for k in FEATURES]], dtype=float)
        p = float(model.predict_proba(x)[0][1])
    except Exception as exc:
        return {"win_probability": None, "passes": True, "gate": gate, "armed": armed,
                "reason": f"scoring failed: {exc}"}
    return {"win_probability": round(p, 4), "passes": p >= gate, "gate": gate, "armed": True,
            "reason": f"P(win)={p:.0%} vs gate {gate:.0%}"}


def status() -> dict[str, Any]:
    m = load_meta()
    wf = m.get("walk_forward") or {}
    # "rows" is progress toward MIN_ROWS — the count of usable journal rows so
    # far, not just what the last (possibly never-run) training saw. Before the
    # first successful train, load_meta() has rows=0 even though the journal is
    # filling up, which read as "no trades taken" on the dashboard.
    try:
        ds = build_dataset()
        collected, collected_live = ds["total_rows"], ds["live_rows"]
    except Exception:
        collected = collected_live = 0
    return {
        "trained_at": m.get("trained_at"),
        "rows": max(int(m.get("rows") or 0), collected),
        "live_rows": max(int(m.get("live_rows") or 0), collected_live),
        "gate_armed": m.get("gate_armed", False),
        "min_win_prob_gate": m.get("min_win_prob_gate", 0.0),
        "oos_delta_usd": wf.get("oos_delta_usd"),
        "kept_fraction": wf.get("kept_fraction"),
        "model_present": MODEL_PATH.is_file(),
        "min_rows": MIN_ROWS,
        "min_live_rows": MIN_LIVE_ROWS,
    }


if __name__ == "__main__":  # self-check — no real journal, no persistence
    # tiny synthetic dataset → train refuses under the row minimum, score fails open
    assert train()["trained"] is False
    v = score({"features": {"asset": "BTCUSD", "side_long": 1.0, "atr_pct": 0.3}})
    assert v["passes"] is True and v["armed"] is False
    st = status()
    assert st["gate_armed"] is False and st["min_rows"] == MIN_ROWS
    # walk_forward math on a hand dataset
    if _sklearn() is not None:
        import numpy as np

        rng = np.random.default_rng(0)
        X = rng.normal(size=(80, len(FEATURES)))
        y = (X[:, 0] > 0).astype(int)
        meta = [{"pnl_usd": 1.0 if yi else -1.0} for yi in y]
        wf = walk_forward(X, y, meta)
        assert "oos_delta_usd" in wf and 0.0 <= wf["suggested_gate"] <= 1.0
    print("crypto.ml.model self-check ok")
