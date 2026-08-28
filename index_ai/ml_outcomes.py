"""Train a lightweight classifier on closed trades; gate new entries by predicted win probability."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import format_ist_display, now_ist_iso

_log = logging.getLogger(__name__)

MODEL_DIR = MEMORY_DIR / "models"
MODEL_PATH = MODEL_DIR / "outcome_model.joblib"
META_PATH = MODEL_DIR / "outcome_meta.json"

MODEL_SCHEMA_VERSION = 2
MIN_TRAINING_SAMPLES = 20
MIN_CLASS_SAMPLES = 5
MIN_HOLDOUT_SAMPLES = 4
DEFAULT_MIN_WIN_PROB_GATE = 0.52
MIN_HOLDOUT_ACCURACY = 0.53
MIN_HOLDOUT_AUC = 0.52

FEATURE_NAMES: tuple[str, ...] = (
    "confidence",
    "is_call",
    "is_credit",
    "is_iron_condor",
    "is_bullish",
    "is_banknifty",
    "ema_spread_pct",
    "dist_tc_pct",
    "dist_bc_pct",
    "cpr_width_pct",
    "volume_ratio",
    "is_ema_cross",
    "is_breakout",
    "pcr",
    "oi_conf_adj",
    "oi_bias_score",
    "hour_ist",
)


def _is_excluded_trade_id(trade_id: str) -> bool:
    tid = trade_id.strip().lower()
    return (
        tid.startswith("test-")
        or tid.startswith("test_")
        or tid == "real-trade-id"
    )


def extract_features(
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    instrument_key: str,
) -> dict[str, float]:
    """Feature vector inputs stored on each trade for consistent retraining."""
    opt = option or {}
    price = float(signal.get("price") or 0)
    tc = float(signal.get("tc") or price)
    bc = float(signal.get("bc") or price)
    ema_fast = float(signal.get("ema_fast") or 0)
    ema_slow = float(signal.get("ema_slow") or 1)
    spread_pct = (ema_fast - ema_slow) / max(abs(ema_slow), 1.0)
    dist_tc = (price - tc) / max(abs(price), 1.0)
    dist_bc = (price - bc) / max(abs(price), 1.0)
    action = str(signal.get("action") or "").upper()
    hour = 12.0
    created = str(
        opt.get("created_at")
        or opt.get("signal_time")
        or signal.get("created_at")
        or signal.get("signal_time")
        or ""
    )
    if "T" in created and len(created) >= 13:
        try:
            hour = float(created[11:13])
        except ValueError:
            pass

    from index_ai.oi_learning import oi_bias_feature

    bias = str(opt.get("chain_bias") or opt.get("oi_bias") or "")
    is_credit = action.startswith("SELL_")
    is_bullish = action in {"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"}
    mode = str(signal.get("strategy_mode") or "").lower()
    breakout = str(signal.get("breakout_tag") or "").upper()
    return {
        "confidence": float(signal.get("confidence") or 0),
        "is_call": 1.0 if "CALL" in action else 0.0,
        "is_credit": 1.0 if is_credit else 0.0,
        "is_iron_condor": 1.0 if action == "SELL_IRON_CONDOR" else 0.0,
        "is_bullish": 1.0 if is_bullish else 0.0,
        "is_banknifty": 1.0 if instrument_key.upper() == "BANKNIFTY" else 0.0,
        "ema_spread_pct": round(spread_pct, 6),
        "dist_tc_pct": round(dist_tc, 6),
        "dist_bc_pct": round(dist_bc, 6),
        "cpr_width_pct": float(signal.get("cpr_width_pct") or 0.0),
        "volume_ratio": float(signal.get("volume_ratio") or 1.0),
        "is_ema_cross": 1.0 if mode == "ema_cross" else 0.0,
        "is_breakout": 1.0 if breakout.startswith("BREAK_") else 0.0,
        "pcr": float(opt.get("chain_pcr") or opt.get("pcr") or 1.0),
        "oi_conf_adj": float(opt.get("oi_confidence_adjustment") or 0.0),
        "oi_bias_score": oi_bias_feature(bias),
        "hour_ist": hour,
    }


def _vectorize(features: dict[str, float]) -> np.ndarray:
    return np.array([[float(features.get(name, 0.0)) for name in FEATURE_NAMES]], dtype=float)


def _features_from_trade(trade: dict[str, Any]) -> dict[str, float] | None:
    option = trade.get("option") or {}
    signal = trade.get("signal") or {}
    if option.get("ml_features"):
        return {k: float(v) for k, v in option["ml_features"].items()}
    inst = str(trade.get("instrument") or option.get("instrument") or "NIFTY")
    return extract_features(signal, option, inst)


def load_training_dataset() -> tuple[np.ndarray, np.ndarray, list[str]]:
    from index_ai.learning import connect, _row_to_trade

    xs: list[list[float]] = []
    ys: list[int] = []
    ids: list[str] = []
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM trades
            WHERE pnl IS NOT NULL
            ORDER BY created_at ASC
            """
        ).fetchall()
    for row in rows:
        trade = _row_to_trade(row)
        tid = str(trade.get("id") or "")
        if _is_excluded_trade_id(tid):
            continue
        feats = _features_from_trade(trade)
        if not feats:
            continue
        pnl = float(trade.get("pnl") or 0)
        xs.append([float(feats.get(name, 0.0)) for name in FEATURE_NAMES])
        ys.append(1 if pnl > 0 else 0)
        ids.append(tid)
    return np.array(xs, dtype=float), np.array(ys, dtype=int), ids


def _load_meta() -> dict[str, Any]:
    if not META_PATH.is_file():
        return {}
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def _save_meta(meta: dict[str, Any]) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401

        return True
    except ImportError:
        return False


def train_outcome_model(*, force: bool = False) -> dict[str, Any]:
    """Retrain logistic model on closed trades; persist versioned weights under memory/models/."""
    if not _sklearn_available():
        return {
            "ready": False,
            "status": "missing_dependency",
            "message": "Install scikit-learn: pip install scikit-learn",
            "min_win_prob_gate": DEFAULT_MIN_WIN_PROB_GATE,
        }

    x, y, trade_ids = load_training_dataset()
    n = len(y)
    wins = int(y.sum()) if n else 0
    losses = n - wins

    if n < MIN_TRAINING_SAMPLES:
        meta = _load_meta()
        return {
            "ready": False,
            "status": "collecting_data",
            "message": f"Need {MIN_TRAINING_SAMPLES}+ closed trades to train (have {n}).",
            "training_samples": n,
            "wins": wins,
            "losses": losses,
            "model_version": meta.get("version"),
            "min_win_prob_gate": float(meta.get("min_win_prob_gate") or DEFAULT_MIN_WIN_PROB_GATE),
        }

    if wins < MIN_CLASS_SAMPLES or losses < MIN_CLASS_SAMPLES:
        return {
            "ready": False,
            "status": "imbalanced",
            "message": f"Need at least {MIN_CLASS_SAMPLES} wins and losses to train (have {wins}/{losses}).",
            "training_samples": n,
            "wins": wins,
            "losses": losses,
            "min_win_prob_gate": DEFAULT_MIN_WIN_PROB_GATE,
        }

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    import joblib

    meta_prev = _load_meta()
    prev_version = int(meta_prev.get("version") or 0)
    if (
        not force
        and meta_prev.get("feature_schema_version") == MODEL_SCHEMA_VERSION
        and prev_version > 0
        and n == int(meta_prev.get("training_samples") or 0)
    ):
        return {**meta_prev, "ready": True, "status": "unchanged", "message": "Model up to date."}

    # The rows arrive oldest-to-newest. Keep the newest observations out of
    # training so validation cannot benefit from future trade outcomes.
    holdout_size = max(MIN_HOLDOUT_SAMPLES, int(np.ceil(n * 0.25)))
    holdout_size = min(holdout_size, n - 2)
    split_at = n - holdout_size
    x_train, x_test = x[:split_at], x[split_at:]
    y_train, y_test = y[:split_at], y[split_at:]
    train_wins = int(y_train.sum())
    train_losses = len(y_train) - train_wins
    if train_wins < MIN_CLASS_SAMPLES or train_losses < MIN_CLASS_SAMPLES:
        return {
            "ready": False,
            "status": "imbalanced_training_window",
            "message": (
                "Need at least "
                f"{MIN_CLASS_SAMPLES} wins and losses before the chronological holdout. "
                f"Have {train_wins}/{train_losses}."
            ),
            "training_samples": n,
            "wins": wins,
            "losses": losses,
            "min_win_prob_gate": DEFAULT_MIN_WIN_PROB_GATE,
        }

    pipeline = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "clf",
                LogisticRegression(max_iter=500, class_weight="balanced", random_state=42),
            ),
        ]
    )
    pipeline.fit(x_train, y_train)
    y_pred = pipeline.predict(x_test)
    accuracy = float(accuracy_score(y_test, y_pred))
    try:
        proba = pipeline.predict_proba(x_test)[:, 1]
        auc = float(roc_auc_score(y_test, proba)) if len(set(y_test)) > 1 else None
        brier = float(brier_score_loss(y_test, proba))
    except Exception:
        auc = None
        brier = None

    coefs = pipeline.named_steps["clf"].coef_[0]
    importance = {
        FEATURE_NAMES[i]: round(float(coefs[i]), 4) for i in range(len(FEATURE_NAMES))
    }
    top_features = sorted(importance.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]

    gate = max(DEFAULT_MIN_WIN_PROB_GATE, float(meta_prev.get("min_win_prob_gate") or 0.0))
    gate_active = bool(
        len(y_test) >= MIN_HOLDOUT_SAMPLES
        and accuracy >= MIN_HOLDOUT_ACCURACY
        and (auc is None or auc >= MIN_HOLDOUT_AUC)
    )
    if gate_active and accuracy >= 0.65:
        gate = min(0.62, gate + 0.02)

    version = prev_version + 1
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)

    meta = {
        "ready": True,
        "status": "trained",
        "version": version,
        "feature_schema_version": MODEL_SCHEMA_VERSION,
        "trained_at": now_ist_iso(),
        "trained_at_ist": format_ist_display(now_ist_iso()),
        "training_samples": n,
        "wins": wins,
        "losses": losses,
        "holdout_accuracy": round(accuracy, 3),
        "holdout_auc": round(auc, 3) if auc is not None else None,
        "holdout_brier": round(brier, 3) if brier is not None else None,
        "validation_method": "chronological_holdout",
        "holdout_samples": len(y_test),
        "gate_active": gate_active,
        "min_win_prob_gate": round(gate, 3),
        "feature_importance": importance,
        "top_features": [{"name": k, "weight": v} for k, v in top_features],
        "message": (
            f"Model v{version} trained on {n} closed trades "
            f"(chronological holdout accuracy {accuracy:.0%}"
            + (f", AUC {auc:.2f}" if auc is not None else "")
            + (
                f"). Blocks entries below {gate:.0%} predicted win rate."
                if gate_active
                else "). Validation is not reliable enough to block entries yet; scoring only."
            )
        ),
    }
    _save_meta(meta)
    _log.info("Outcome ML model v%s trained (%s samples, accuracy %.1f%%)", version, n, accuracy * 100)
    return meta


def load_ml_status() -> dict[str, Any]:
    meta = _load_meta()
    if (
        meta.get("ready")
        and meta.get("feature_schema_version") == MODEL_SCHEMA_VERSION
        and MODEL_PATH.is_file()
    ):
        return meta
    if meta.get("ready") and meta.get("feature_schema_version") != MODEL_SCHEMA_VERSION:
        return {
            "ready": False,
            "status": "stale_feature_schema",
            "message": "Outcome model uses an older feature schema; retraining is required.",
            "min_win_prob_gate": DEFAULT_MIN_WIN_PROB_GATE,
        }
    if meta:
        return meta
    return {
        "ready": False,
        "status": "not_trained",
        "message": "No model yet — closes more trades to auto-train.",
        "min_win_prob_gate": DEFAULT_MIN_WIN_PROB_GATE,
    }


def predict_win_probability(
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    instrument_key: str,
) -> dict[str, Any]:
    """Return ML win probability for a proposed setup (no side effects)."""
    meta = load_ml_status()
    if not meta.get("ready") or not MODEL_PATH.is_file():
        return {
            "ready": False,
            "win_probability": None,
            "model_version": meta.get("version"),
            "message": meta.get("message") or "Model not trained.",
        }
    if not _sklearn_available():
        return {"ready": False, "win_probability": None, "message": "scikit-learn not installed."}

    import joblib

    pipeline = joblib.load(MODEL_PATH)
    feats = extract_features(signal, option, instrument_key)
    vec = _vectorize(feats)
    proba = float(pipeline.predict_proba(vec)[0][1])
    gate = float(meta.get("min_win_prob_gate") or DEFAULT_MIN_WIN_PROB_GATE)
    return {
        "ready": True,
        "win_probability": round(proba, 3),
        "passes_ml_gate": proba >= gate,
        "gate_active": bool(meta.get("gate_active")),
        "min_win_prob_gate": gate,
        "model_version": meta.get("version"),
        "holdout_accuracy": meta.get("holdout_accuracy"),
        "features": feats,
    }


def score_trade_setup(
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    instrument_key: str,
) -> dict[str, Any]:
    """Alias used by execution planner."""
    return predict_win_probability(signal, option, instrument_key)
