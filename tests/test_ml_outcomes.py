from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from index_ai.learning import init_db, record_trade, record_trade_outcome
from index_ai import ml_outcomes
from index_ai.ml_outcomes import (
    extract_features,
    load_ml_status,
    predict_win_probability,
    train_outcome_model,
)


@pytest.fixture
def ml_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "ml_test.sqlite"
    models = tmp_path / "models"
    monkeypatch.setenv("INDEX_AI_DB", str(db))
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    monkeypatch.setattr("index_ai.ml_outcomes.MODEL_DIR", models)
    monkeypatch.setattr("index_ai.ml_outcomes.MODEL_PATH", models / "outcome_model.joblib")
    monkeypatch.setattr("index_ai.ml_outcomes.META_PATH", models / "outcome_meta.json")
    init_db()
    if ml_outcomes.MODEL_PATH.exists():
        ml_outcomes.MODEL_PATH.unlink()
    if ml_outcomes.META_PATH.exists():
        ml_outcomes.META_PATH.unlink()


def _seed_closed_trades(n: int = 12) -> None:
    for i in range(n):
        win = i % 3 != 0
        signal = {
            "action": "BUY_CALL" if win else "BUY_PUT",
            "confidence": 0.6 + (0.05 if win else -0.1),
            "price": 24000.0 + i,
            "pivot": 23900,
            "bc": 23850,
            "tc": 23950,
            "ema_fast": 24010 if win else 23990,
            "ema_slow": 23980,
        }
        option = {
            "instrument": "NIFTY",
            "chain_pcr": 1.1 if win else 0.9,
            "ml_features": extract_features(signal, {}, "NIFTY"),
        }
        tid = record_trade(
            mode="PAPER",
            instrument="NIFTY",
            action=str(signal["action"]),
            confidence=float(signal["confidence"]),
            option=option,
            signal=signal,
            status="PAPER_RECORDED",
        )
        record_trade_outcome(tid, 100.0 if win else -80.0, note="test seed")


def test_extract_features_shape() -> None:
    feats = extract_features(
        {
            "action": "BUY_CALL",
            "confidence": 0.72,
            "price": 24100,
            "tc": 24050,
            "bc": 23950,
            "ema_fast": 24110,
            "ema_slow": 24080,
        },
        {"chain_pcr": 1.15},
        "NIFTY",
    )
    assert feats["is_call"] == 1.0
    assert feats["is_banknifty"] == 0.0


def test_train_needs_minimum_samples(ml_db: None) -> None:
    result = train_outcome_model(force=True)
    assert result["ready"] is False
    assert result["status"] in ("collecting_data", "imbalanced")


def test_train_and_predict(ml_db: None) -> None:
    _seed_closed_trades(24)
    trained = train_outcome_model(force=True)
    assert trained["ready"] is True
    assert trained["version"] >= 1
    assert trained["holdout_accuracy"] is not None
    assert trained["validation_method"] == "chronological_holdout"
    assert trained["holdout_samples"] >= 4

    pred = predict_win_probability(
        {
            "action": "BUY_CALL",
            "confidence": 0.8,
            "price": 24100,
            "tc": 24000,
            "bc": 23900,
            "ema_fast": 24120,
            "ema_slow": 24000,
        },
        {"chain_pcr": 1.2},
        "NIFTY",
    )
    assert pred["ready"] is True
    assert pred["win_probability"] is not None
    assert 0.0 <= pred["win_probability"] <= 1.0

    status = load_ml_status()
    assert status["ready"] is True
    assert ml_outcomes.META_PATH.is_file()
    assert ml_outcomes.MODEL_PATH.is_file()
