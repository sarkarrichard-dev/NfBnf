from __future__ import annotations

import pytest

from index_ai.instruments import get_instrument
from index_ai.learning import format_trade_for_ui, resolve_current_option_ltp, resolve_trade_lot_size


@pytest.fixture
def nse_2026_lots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Current NSE lots (Dec 2025 revision) — override legacy .env values in CI/dev."""
    monkeypatch.setenv("NIFTY_LOT_SIZE", "65")
    monkeypatch.setenv("BANKNIFTY_LOT_SIZE", "30")


def test_nifty_lot_size_default_is_65(nse_2026_lots: None) -> None:
    assert get_instrument("NIFTY").lot_size == 65


def test_banknifty_lot_size_default_is_30(nse_2026_lots: None) -> None:
    assert get_instrument("BANKNIFTY").lot_size == 30


def test_resolve_trade_lot_corrects_stored_75(nse_2026_lots: None) -> None:
    trade = {
        "instrument": "NIFTY",
        "option": {"quantity": 75, "instrument": "NIFTY"},
    }
    configured, effective = resolve_trade_lot_size(trade)
    assert configured == 65
    assert effective == 65


def test_infer_current_ltp_from_mtm() -> None:
    ltp = resolve_current_option_ltp(
        {"ltp": 100.0, "mtm_pnl": -650.0},
        is_open=True,
        mtm_pnl=-650.0,
        entry_ltp=100.0,
        qty=65,
        tx="BUY",
    )
    assert ltp is not None
    assert abs(ltp - 90.0) < 0.01


def test_format_trade_exposes_current_premium(nse_2026_lots: None) -> None:
    row = format_trade_for_ui(
        {
            "id": "t1",
            "instrument": "NIFTY",
            "action": "BUY_CALL",
            "mode": "PAPER",
            "status": "PAPER_RECORDED",
            "created_at": "2026-05-26T10:00:00+05:30",
            "option": {
                "option_type": "CALL",
                "strike": 24050,
                "ltp": 56.8,
                "quantity": 75,
                "transaction_type": "BUY",
                "mtm_pnl": -738.75,
            },
            "signal": {"action": "BUY_CALL", "price": 24064},
        }
    )
    assert row["lot_label"] == "1 lot · 65 qty"
    assert row["current_option_ltp"] is not None
