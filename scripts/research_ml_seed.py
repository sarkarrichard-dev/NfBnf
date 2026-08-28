"""
Train a *seed* win-probability model on the backtested trade log.

Reads research/datasets/backtest_trades.jsonl (from scripts.research_backtest),
builds the same feature vector index_ai.ml_outcomes uses, fits the same
StandardScaler + LogisticRegression with a chronological hold-out, and writes the
model + a report to research/models/.

    python -m scripts.research_ml_seed

This is a RESEARCH artifact. It is NOT wired into the live ML gate — the live
gate still trains only on real closed trades (memory/models/). Use this to see
which features carry signal in the backtest and whether a model beats a coin
flip out-of-sample before you invest in it.
"""

from __future__ import annotations

import json
from pathlib import Path

DATASET = Path("research/datasets/backtest_trades.jsonl")
OUT = Path("research/models")


def main() -> None:
    if not DATASET.exists():
        raise SystemExit("Run scripts.research_backtest first (no backtest_trades.jsonl).")
    try:
        import joblib
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        raise SystemExit("pip install scikit-learn joblib")

    from index_ai.ml_outcomes import FEATURE_NAMES, extract_features

    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
    rows.sort(key=lambda t: str(t.get("entry_time") or t.get("signal_time") or ""))

    xs, ys = [], []
    for t in rows:
        sf = t.get("signal_features") or {}
        signal = {
            "action": t.get("action"),
            "confidence": t.get("confidence"),
            "strategy_mode": t.get("strategy_mode"),
            "signal_time": t.get("signal_time"),
            **sf,
        }
        feats = extract_features(signal, {}, str(t.get("instrument") or "NIFTY"))
        xs.append([float(feats.get(n, 0.0)) for n in FEATURE_NAMES])
        ys.append(1 if float(t.get("proxy_pnl_rupees") or 0) > 0 else 0)

    x, y = np.array(xs, dtype=float), np.array(ys, dtype=int)
    n = len(y)
    if n < 40:
        raise SystemExit(f"Only {n} backtest trades — need 40+. Run research_backtest on more history.")

    holdout = max(10, int(np.ceil(n * 0.25)))
    split = n - holdout
    pipe = Pipeline([("scale", StandardScaler()),
                     ("clf", LogisticRegression(max_iter=500, class_weight="balanced", random_state=42))])
    pipe.fit(x[:split], y[:split])
    proba = pipe.predict_proba(x[split:])[:, 1]
    pred = (proba >= 0.5).astype(int)
    acc = float(accuracy_score(y[split:], pred))
    auc = float(roc_auc_score(y[split:], proba)) if len(set(y[split:])) > 1 else None
    brier = float(brier_score_loss(y[split:], proba))

    coefs = pipe.named_steps["clf"].coef_[0]
    importance = sorted(
        ((FEATURE_NAMES[i], round(float(coefs[i]), 4)) for i in range(len(FEATURE_NAMES))),
        key=lambda kv: abs(kv[1]), reverse=True,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, OUT / "seed_outcome_model.joblib")
    meta = {
        "source": "backtest_trades.jsonl",
        "training_samples": n,
        "wins": int(y.sum()),
        "losses": int(n - y.sum()),
        "holdout_samples": holdout,
        "holdout_accuracy": round(acc, 3),
        "holdout_auc": round(auc, 3) if auc is not None else None,
        "holdout_brier": round(brier, 3),
        "beats_coin_flip": bool(acc > 0.55 and (auc is None or auc > 0.55)),
        "feature_importance": dict(importance),
    }
    (OUT / "seed_outcome_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    report = [
        "# Seed win-probability model (backtest-trained)\n",
        f"- Trained on **{n}** backtested trades ({int(y.sum())} wins / {int(n - y.sum())} losses)",
        f"- Chronological hold-out: **{holdout}** newest trades",
        f"- Hold-out accuracy: **{acc:.1%}**"
        + (f", AUC **{auc:.2f}**" if auc is not None else "")
        + f", Brier **{brier:.3f}**",
        f"- Beats a coin flip out-of-sample: **{'yes' if meta['beats_coin_flip'] else 'no'}**\n",
        "## Feature weights (standardised, |weight| desc)\n",
        "| feature | weight |", "|---|--:|",
        *[f"| {k} | {v} |" for k, v in importance],
        "\n_Not wired to the live gate. If accuracy/AUC are near 50%, the strategies "
        "have no learnable edge in this feature space — which is the expected result._",
    ]
    (Path("research/reports") / "ml_seed.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print("\nWrote research/models/seed_outcome_model.joblib and research/reports/ml_seed.md")


if __name__ == "__main__":
    main()
