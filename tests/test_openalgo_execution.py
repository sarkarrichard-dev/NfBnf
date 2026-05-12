from __future__ import annotations

import pytest

from trading_ai_engine.openalgo.symbols import yahoo_to_openalgo
from trading_ai_engine.openalgo.url import normalize_openalgo_base_url
from trading_ai_engine.server import db as dbmod
from trading_ai_engine.trading import execution_mode as em


pytestmark = pytest.mark.unit


def test_normalize_openalgo_localhost_http() -> None:
    assert normalize_openalgo_base_url("http://127.0.0.1:5000") == "http://127.0.0.1:5000"


def test_normalize_openalgo_private_http() -> None:
    assert normalize_openalgo_base_url("http://192.168.1.10:5000/") == "http://192.168.1.10:5000"


def test_normalize_openalgo_rejects_http_public_hostname() -> None:
    with pytest.raises(ValueError, match="http"):
        normalize_openalgo_base_url("http://example.com")


def test_yahoo_equity_to_openalgo() -> None:
    assert yahoo_to_openalgo("TCS.NS", instrument_type="equity") == ("TCS", "NSE")


def test_yahoo_index_nsei() -> None:
    assert yahoo_to_openalgo("^NSEI", instrument_type="equity") == ("NIFTY", "NSE_INDEX")


def test_get_execution_mode_respects_db_pref(monkeypatch) -> None:
    monkeypatch.setattr(dbmod, "get_operator_pref", lambda key: "paper_local" if key == "execution_mode" else None)
    assert em.get_execution_mode() == em.MODE_PAPER_LOCAL


def test_set_execution_mode_invalid(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "op_prefs.sqlite")
    dbmod.init_db()
    out = em.set_execution_mode("not_a_mode")
    assert out.get("ok") is False


def test_place_paper_order_live_dhan_rejected(monkeypatch) -> None:
    from trading_ai_engine.trading import paper as paper_mod

    monkeypatch.setattr(paper_mod, "get_execution_mode", lambda: em.MODE_LIVE_DHAN)
    monkeypatch.setattr(paper_mod, "paper_placement_allowed", lambda **kw: (True, "", {}))
    monkeypatch.setattr(paper_mod, "load_market_model", lambda: {})
    out = paper_mod.place_paper_order(
        finding_id="00000000-0000-0000-0000-000000000001",
        symbol="TCS.NS",
        plan={
            "eligible": True,
            "side": "long",
            "quantity": 1,
            "entry_price": 100.0,
            "notional": 100.0,
            "risk_amount": 1.0,
            "instrument_type": "equity",
        },
        brain={},
    )
    assert out["status"] == "rejected"
    assert out["reason"] == "live_dhan_router_not_implemented"
