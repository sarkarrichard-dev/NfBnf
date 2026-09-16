from __future__ import annotations

import threading
import time

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

    option = {
        "transaction_type": "SELL",
        "ltp": 77.35,
        "quantity": 30,
        "expiry": "2026-05-30",
        "legs": [
            {
                "transaction_type": "SELL",
                "option_type": "PUT",
                "strike": 54600,
                "ltp": 100.0,
                "security_id": 1,
                "segment": "NSE_FNO",
                "quantity": 30,
            },
            {
                "transaction_type": "BUY",
                "option_type": "PUT",
                "strike": 54400,
                "ltp": 22.65,
                "security_id": 2,
                "segment": "NSE_FNO",
                "quantity": 30,
            },
        ],
    }
    from index_ai.learning import record_trade

    trade_id = record_trade(
        mode="PAPER",
        instrument="BANKNIFTY",
        action="SELL_BULL_PUT_SPREAD",
        confidence=0.6,
        option=option,
        signal={"price": 54800.0},
        status="PAPER_RECORDED",
    )
    trade = {
        "id": trade_id,
        "mode": "PAPER",
        "instrument": "BANKNIFTY",
        "action": "SELL_BULL_PUT_SPREAD",
        "signal": {"price": 54800.0},
        "option": option,
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


def test_close_open_trade_never_double_places_a_live_exit_order(monkeypatch) -> None:
    """2026-09-16 trading-safety review, after adding manual Close buttons:
    close_open_trade's own "already closed?" check is read-then-act with no
    lock, so two near-simultaneous callers for the same LIVE trade (a manual
    Close click racing the automatic trailing-stop sweep, a double-click)
    could both pass that check before either commits — two real opposite-
    side orders for one position. The per-trade_id lock added in exit.py
    must serialize them so the broker only ever sees one."""
    from index_ai.config import AppSettings, DhanSettings, RiskSettings
    from index_ai.learning import record_trade

    cfg = AppSettings(
        dhan=DhanSettings("1", "tok", "https://api.dhan.co/v2", "", "", "", ""),
        risk=RiskSettings(
            trading_mode="LIVE",
            allow_live_trading=True,
            allow_option_buying=True,
            allow_option_selling=True,
            max_losing_trades_per_day=3,
            max_daily_loss_rupees=6000.0,
            trailing_stop_index_points=40.0,
            min_confidence=0.55,
            max_profit_cap_rupees=None,
        ),
    )
    option = {
        "transaction_type": "BUY",
        "option_type": "CALL",
        "strike": 24000,
        "ltp": 100.0,
        "security_id": 1,
        "segment": "NSE_FNO",
        "quantity": 75,
    }
    trade_id = record_trade(
        mode="LIVE",
        instrument="NIFTY",
        action="BUY_CALL",
        confidence=0.7,
        option=option,
        signal={"price": 24000.0},
        status="LIVE_PLACED",
    )
    trade = {
        "id": trade_id,
        "mode": "LIVE",
        "instrument": "NIFTY",
        "action": "BUY_CALL",
        "status": "LIVE_PLACED",
        "signal": {"price": 24000.0},
        "option": option,
    }

    from index_ai.execution_safety import SafetyCheck

    monkeypatch.setattr(
        "index_ai.execution_safety.validate_live_exit_allowed",
        lambda *a, **k: SafetyCheck(True, "ok", "ok"),
    )

    calls: list[int] = []

    def slow_place_live_exit(*a, **k):
        # widen the race window a live broker round-trip would naturally have
        calls.append(1)
        time.sleep(0.1)
        return {"order_id": "abc"}

    monkeypatch.setattr("index_ai.dhan_orders.place_live_exit_orders", slow_place_live_exit)
    monkeypatch.setattr("index_ai.dhan_orders.live_orders_enabled", lambda *a, **k: True)

    class _Client:
        pass

    results: list[dict] = []

    def close_once():
        results.append(
            close_open_trade(dict(trade), client=_Client(), app_settings=cfg, reason="race")
        )

    threads = [threading.Thread(target=close_once) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, "the broker must see exactly one exit order, not two"
    statuses = sorted(r["status"] for r in results)
    assert statuses == ["ALREADY_CLOSED", "CLOSED"]
