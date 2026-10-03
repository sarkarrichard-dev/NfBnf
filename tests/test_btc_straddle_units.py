"""BTC straddle money maths: option premiums are quoted per 1 BTC, a contract is
0.001 BTC, so money = premium x size x contract_value. (Until 2026-10-03 the
paper P&L was 1000x too big: a 10-contract straddle showed +$1,249 on ~$50.)"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from crypto import btc_straddle as bs


def _pos(**kw):
    pos = {
        "call_symbol": "C-BTC-84800-041026",
        "put_symbol": "P-BTC-84800-041026",
        "strike": 84800.0,
        "call_entry": 305.54324971,
        "put_entry": 238.76863012,
        "size": 10,
        "total_credit": 5443.1187983,  # the old, 1000x-inflated stored figure
        "tp_fraction": 0.5,
        "sl_fraction": 1.0,
        "entry_time": "2026-10-03T12:31:02+00:00",
        "opened_at": "2026-10-03T12:31:02+00:00",
        "settlement": "2026-10-04T12:00:00+00:00",
        "day": "2026-10-03",
        "entry_spot": 84814.12,
        "contract_value": 0.001,
    }
    pos.update(kw)
    return pos


def _marks(monkeypatch, call, put):
    monkeypatch.setattr(
        bs, "_leg_price", lambda sym, client: call if sym.startswith("C-") else put
    )


def test_unrealized_is_dollars_not_thousands(monkeypatch):
    _marks(monkeypatch, 235.57660623, 183.42196889)  # the real marks on 2026-10-03
    info = bs.unrealized(_pos(), None)
    # (544.31 - 419.0) per BTC x 0.01 BTC = about $1.25 gross, minus a little fee
    assert 0 < info["unrealized_gross_usd"] < 2
    assert abs(info["unrealized_usd"]) < 5


def test_exit_row_matches_the_real_loss_in_dollars():
    pos = _pos(
        call_entry=646.63165269,
        put_entry=527.37710832,
        entry_spot=86659.99976437,
        day="2026-10-02",
    )
    row = bs._build_exit_row(
        pos,
        {
            "call_exit": 8.58987804,
            "put_exit": 2527.50008147,
            "reason": "stop_loss",
            "exit_time": datetime(2026, 10, 2, 18, 40, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert row["total_credit_usd"] == pytest.approx(11.74, abs=0.01)
    assert row["exit_debit_usd"] == pytest.approx(25.36, abs=0.01)
    assert row["gross_usd"] == pytest.approx(-13.62, abs=0.01)
    assert -20 < row["pnl_usd"] < -13  # gross loss plus a ~$2 fee, not -$13,600


def test_take_profit_and_stop_loss_use_the_same_units_as_the_marks(monkeypatch):
    now = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
    _marks(monkeypatch, 140.0, 130.0)  # 270 left of 544 credit: more than half kept
    assert bs._manage_position(_pos(), None, now)["reason"] == "take_profit"
    _marks(monkeypatch, 160.0, 150.0)  # 310 left: not yet half
    assert bs._manage_position(_pos(), None, now) is None
    _marks(monkeypatch, 700.0, 450.0)  # debit 1150 vs credit 544: loss > credit
    assert bs._manage_position(_pos(), None, now)["reason"] == "stop_loss"


def test_open_position_with_old_inflated_total_credit_is_still_read_correctly(monkeypatch):
    _marks(monkeypatch, 235.0, 183.0)
    # the stored total_credit (5443) must be ignored: credit comes from the entry prices
    assert bs._credit(_pos()) == pytest.approx(5.4431, abs=0.001)
