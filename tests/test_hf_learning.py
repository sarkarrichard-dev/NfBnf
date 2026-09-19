from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from index_ai.hf_learning import (
    build_setup_narrative,
    score_setup_hf,
    sync_hf_dataset,
    _parse_sentiment_result,
)


def test_build_setup_narrative_includes_action() -> None:
    text = build_setup_narrative(
        {
            "action": "BUY_CALL",
            "confidence": 0.71,
            "reason": "Above CPR top.",
            "price": 24100,
            "tc": 24050,
            "bc": 23950,
            "ema_fast": 24110,
            "ema_slow": 24080,
        },
        {"option_type": "CALL", "strike": 24100, "chain_pcr": 1.1},
        "NIFTY",
    )
    assert "BUY_CALL" in text
    assert "NIFTY" in text


def test_parse_finbert_style_result() -> None:
    parsed = _parse_sentiment_result(
        [[{"label": "positive", "score": 0.82}, {"label": "negative", "score": 0.18}]]
    )
    assert parsed["label"] == "positive"
    assert parsed["positive_prob"] > 0.8


def test_score_setup_hf_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("index_ai.hf_learning._hf_token", lambda: "")
    monkeypatch.delenv("HF_USE_LOCAL", raising=False)
    result = score_setup_hf(
        {
            "action": "BUY_CALL",
            "confidence": 0.7,
            "reason": "test",
            "price": 1,
            "tc": 1,
            "bc": 1,
            "ema_fast": 1,
            "ema_slow": 0,
        },
        {},
        "NIFTY",
    )
    assert result["ready"] is False
    assert result["status"] == "no_token"


def test_sync_hf_dataset_empty_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "hf.sqlite"
    hf_dir = tmp_path / "hf"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    monkeypatch.setattr("index_ai.hf_learning.HF_DIR", hf_dir)
    monkeypatch.setattr("index_ai.hf_learning.DATASET_PATH", hf_dir / "outcomes.jsonl")
    monkeypatch.setattr("index_ai.hf_learning.META_PATH", hf_dir / "hf_meta.json")
    from index_ai.learning import init_db

    init_db()
    out = sync_hf_dataset()
    assert out["rows"] == 0


def test_sync_hf_dataset_survives_concurrent_callers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: executor.py/scanner.py now call into this from separate
    threads (asyncio.to_thread). Before the _HF_LOCK fix this reproducibly
    threw PermissionError on the shared hf_meta.json.tmp under real concurrency."""
    db = tmp_path / "hf.sqlite"
    hf_dir = tmp_path / "hf"
    monkeypatch.setattr("index_ai.config.DB_PATH", db)
    monkeypatch.setattr("index_ai.learning.DB_PATH", db)
    monkeypatch.setattr("index_ai.hf_learning.HF_DIR", hf_dir)
    monkeypatch.setattr("index_ai.hf_learning.DATASET_PATH", hf_dir / "outcomes.jsonl")
    monkeypatch.setattr("index_ai.hf_learning.META_PATH", hf_dir / "hf_meta.json")
    from index_ai.learning import init_db

    init_db()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: sync_hf_dataset(), range(20)))
    assert all(r["rows"] == 0 for r in results)
