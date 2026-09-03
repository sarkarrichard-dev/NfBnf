from __future__ import annotations

from index_ai.config import AppSettings, DhanSettings, RiskSettings
from index_ai.executor import build_execution_plan
from index_ai.instruments import get_instrument
from index_ai.strategies.strategy import StrategySignal


def _settings() -> AppSettings:
    return AppSettings(
        dhan=DhanSettings("", "", "", "", "", "", ""),
        risk=RiskSettings(
            trading_mode="PAPER",
            allow_live_trading=False,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=40.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )


def test_credit_plan_uses_credit_confidence_not_learned_buy_gate(monkeypatch) -> None:
    monkeypatch.setattr("index_ai.market_clock.is_trading_entries_allowed", lambda when=None: True)
    monkeypatch.setattr("index_ai.market_clock.is_entry_session_timestamp", lambda when=None: True)
    monkeypatch.setattr("index_ai.market_clock.trading_window_message", lambda when=None: "ok")
    monkeypatch.setattr(
        "index_ai.executor.learned_settings",
        lambda: {"effective_min_confidence": 0.65, "min_confidence_adjustment": 0.10},
    )
    monkeypatch.setattr(
        "index_ai.executor.score_trade_setup",
        lambda *a, **k: {"ready": True, "win_probability": 0.1},
    )
    monkeypatch.setattr(
        "index_ai.executor.score_setup_hf",
        lambda *a, **k: {"ready": True, "block_setup": True},
    )

    signal = StrategySignal(
        action="SELL_BULL_PUT_SPREAD",
        reason="test",
        confidence=0.62,
        price=24000.0,
        pivot=23900.0,
        bc=23880.0,
        tc=23920.0,
        ema_fast=24010.0,
        ema_slow=23950.0,
    )
    option = {
        "instrument": "NIFTY",
        "structure": "BULL_PUT_SPREAD",
        "transaction_type": "SELL",
        "quantity": 65,
        "security_id": 1,
        "segment": "NSE_FNO",
        "legs": [
            {
                "transaction_type": "SELL",
                "option_type": "PUT",
                "strike": 23900,
                "security_id": 1,
                "segment": "NSE_FNO",
                "quantity": 65,
            },
            {
                "transaction_type": "BUY",
                "option_type": "PUT",
                "strike": 23850,
                "security_id": 2,
                "segment": "NSE_FNO",
                "quantity": 65,
            },
        ],
    }
    plan = build_execution_plan(
        app_settings=_settings(),
        instrument=get_instrument("NIFTY"),
        signal=signal,
        option=option,
    )
    assert plan.allowed is True
    assert "Credit structure" in plan.reason


def _sell_signal() -> StrategySignal:
    return StrategySignal(
        action="SELL_BULL_PUT_SPREAD", reason="t", confidence=0.62, price=24000.0,
        pivot=23900.0, bc=23880.0, tc=23920.0, ema_fast=24010.0, ema_slow=23950.0,
    )


def _sell_option() -> dict:
    return {
        "instrument": "NIFTY", "structure": "BULL_PUT_SPREAD", "transaction_type": "SELL",
        "quantity": 65, "security_id": 1, "segment": "NSE_FNO",
        "net_credit_points": 20.0, "max_loss_points": 30.0,
        "legs": [
            {"transaction_type": "SELL", "option_type": "PUT", "strike": 23900,
             "security_id": 1, "segment": "NSE_FNO", "quantity": 65},
            {"transaction_type": "BUY", "option_type": "PUT", "strike": 23850,
             "security_id": 2, "segment": "NSE_FNO", "quantity": 65},
        ],
    }


def _prep(monkeypatch, win_prob: float, model_gate: float) -> None:
    for fn in ("is_trading_entries_allowed", "is_entry_session_timestamp"):
        monkeypatch.setattr(f"index_ai.market_clock.{fn}", lambda when=None: True)
    monkeypatch.setattr("index_ai.executor.learned_settings", lambda: {})
    monkeypatch.setattr("index_ai.executor.score_setup_hf", lambda *a, **k: {"ready": False})
    monkeypatch.setattr(
        "index_ai.executor.score_trade_setup",
        lambda *a, **k: {
            "ready": True, "win_probability": win_prob, "gate_active": True,
            "min_win_prob_gate": model_gate, "model_version": 99,
        },
    )


def test_sell_ml_gate_capped_at_band_top(monkeypatch) -> None:
    # model wants 80%, but sell band caps the gate at 65% — a 66% setup passes
    _prep(monkeypatch, win_prob=0.66, model_gate=0.80)
    plan = build_execution_plan(
        app_settings=_settings(), instrument=get_instrument("NIFTY"),
        signal=_sell_signal(), option=_sell_option(),
    )
    assert plan.allowed is True


def test_sell_ml_gate_blocks_below_band(monkeypatch) -> None:
    _prep(monkeypatch, win_prob=0.60, model_gate=0.80)
    plan = build_execution_plan(
        app_settings=_settings(), instrument=get_instrument("NIFTY"),
        signal=_sell_signal(), option=_sell_option(),
    )
    assert plan.allowed is False and "gate 65%" in plan.reason
