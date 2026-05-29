from __future__ import annotations

from index_ai.exit import close_open_trade


def test_close_credit_spread_imports_compute_mtm(monkeypatch) -> None:
    """Regression: close_open_trade must import is_credit_option (scanner close_error)."""
    from index_ai.config import AppSettings, DhanSettings, RiskSettings

    cfg = AppSettings(
        dhan=DhanSettings("1", "tok", "https://api.dhan.co/v2", "", "", "", ""),
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

    trade = {
        "id": "t-credit",
        "mode": "PAPER",
        "instrument": "BANKNIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "signal": {"price": 54800.0},
        "option": {
            "transaction_type": "SELL",
            "ltp": 77.35,
            "quantity": 30,
            "expiry": "2026-05-30",
            "legs": [
                {"transaction_type": "SELL", "option_type": "PUT", "strike": 54600, "ltp": 100.0, "security_id": 1, "segment": "NSE_FNO", "quantity": 30},
                {"transaction_type": "BUY", "option_type": "PUT", "strike": 54400, "ltp": 22.65, "security_id": 2, "segment": "NSE_FNO", "quantity": 30},
            ],
        },
    }

    monkeypatch.setattr(
        "index_ai.exit.compute_credit_mtm",
        lambda option, client, **kw: (142.5, 72.6, [95.0, 22.4]),
    )

    class _Client:
        pass

    result = close_open_trade(trade, client=_Client(), app_settings=cfg, reason="test exit")
    assert result["status"] == "CLOSED"
    assert result["pnl"] == 142.5
