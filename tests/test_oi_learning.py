from __future__ import annotations

from index_ai.learning import record_trade, record_trade_outcome, update_learning
from index_ai.oi_learning import analyze_oi_outcomes, oi_bias_feature


def test_oi_bias_feature_codes() -> None:
    assert oi_bias_feature("call_heavy") == 1.0
    assert oi_bias_feature("put_heavy") == -1.0
    assert oi_bias_feature("neutral") == 0.0


def test_analyze_oi_outcomes_from_closed_trade() -> None:
    tid = record_trade(
        mode="PAPER",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.72,
        option={
            "chain_pcr": 1.2,
            "chain_bias": "put_heavy",
            "oi_confidence_adjustment": -0.08,
        },
        signal={"action": "BUY_CALL", "confidence": 0.72, "price": 24000},
        status="CLOSED",
    )
    record_trade_outcome(tid, 500.0)
    insights = analyze_oi_outcomes(min_samples=1)
    assert insights["trades_with_oi"] >= 1
    assert insights["message"]


def test_learning_includes_oi_insights() -> None:
    learned = update_learning()
    assert "oi_insights" in learned
    assert "by_bias" in learned["oi_insights"]
