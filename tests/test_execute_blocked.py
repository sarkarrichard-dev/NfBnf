from __future__ import annotations

from unittest.mock import MagicMock

from index_ai.config import AppSettings, DhanSettings, RiskSettings
from index_ai.executor import ExecutionPlan, execute_plan


def _live_settings() -> AppSettings:
    return AppSettings(
        dhan=DhanSettings(
            client_id="1",
            access_token="tok",
            api_base_url="https://api.dhan.co/v2",
            api_key="k",
            api_secret="s",
            auth_base_url="https://auth.dhan.co",
            token_expiry="",
        ),
        risk=RiskSettings(
            trading_mode="LIVE",
            allow_live_trading=True,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=1.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )


def test_execute_live_blocked_when_allow_live_false(monkeypatch) -> None:
    plan = ExecutionPlan(
        allowed=True,
        mode="LIVE",
        reason="ok",
        option={
            "instrument": "NIFTY",
            "security_id": 1,
            "segment": "NSE_FNO",
            "transaction_type": "BUY",
            "quantity": 65,
            "ltp": 100.0,
        },
        signal={"action": "BUY_CALL", "confidence": 0.7, "price": 24000},
    )
    cfg = _live_settings()
    cfg = AppSettings(
        dhan=cfg.dhan,
        risk=RiskSettings(
            trading_mode="LIVE",
            allow_live_trading=False,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=1.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )

    def fake_settings() -> AppSettings:
        return cfg

    monkeypatch.setattr("index_ai.config.settings", fake_settings)
    monkeypatch.setattr("index_ai.risk.kill_switch_state", lambda r: {"active": False, "reasons": []})
    monkeypatch.setattr("index_ai.market_clock.is_entry_session_timestamp", lambda when=None: True)

    result = execute_plan(plan, cfg, MagicMock())
    assert result["status"] == "BLOCKED"
    assert "ALLOW_LIVE_TRADING" in result["reason"]
