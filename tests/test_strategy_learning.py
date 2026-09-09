"""The confidence ladder + entry-pattern flags."""

from __future__ import annotations

from index_ai import strategy_learning as sl


def _india(pnl, side, hour, day, **sig):
    from index_ai.strategies.strategy_router import trade_lane  # noqa: F401

    return {
        "instrument": "NIFTY",
        "action": "BUY_CALL" if side == "long" else "BUY_PUT",
        "mode": "PAPER",
        "pnl": pnl,
        "created_at": f"{day}T{hour:02d}:00:00",
        "signal": {"strategy_mode": "candlestick_buy", **sig},
        "option": {},
    }


def test_ladder_states(monkeypatch):
    monkeypatch.setattr(sl, "data_epoch", lambda: None)

    def rows(n):
        return [
            _india(10.0 if i % 2 else -8.0, "long", 10, f"2026-09-{(i % 27) + 1:02d}")
            for i in range(n)
        ]

    import index_ai.learning as learning

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: rows(8))
    assert sl.learning_report()["india"][0]["state"] == "watching"
    assert sl.learning_report()["india"][0]["observations"] == []

    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: rows(25))
    assert sl.learning_report()["india"][0]["state"] == "observing"

    monkeypatch.setattr(
        learning,
        "recent_trades",
        lambda limit=0: [
            _india(10.0, "long", 10, f"2026-{m:02d}-{d:02d}")
            for m in (7, 8, 9)
            for d in range(1, 16)
        ],
    )
    r = sl.learning_report()["india"][0]
    assert r["state"] == "ready" and r["trading_days"] >= 15


def test_flags_a_side_skew(monkeypatch):
    monkeypatch.setattr(sl, "data_epoch", lambda: None)
    import index_ai.learning as learning

    rows = [_india(50.0, "short", 10, f"2026-09-{d:02d}") for d in range(1, 21)] + [
        _india(-40.0, "long", 10, f"2026-10-{d:02d}") for d in range(1, 21)
    ]
    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: rows)
    obs = sl.learning_report()["india"][0]["observations"]
    assert any("long side is the drag" in o for o in obs), obs


def test_frozen_when_recently_positive(monkeypatch):
    monkeypatch.setattr(sl, "data_epoch", lambda: None)
    import index_ai.learning as learning

    rows = [_india(30.0, "long", 10, f"2026-09-{d:02d}") for d in range(1, 21)]
    monkeypatch.setattr(learning, "recent_trades", lambda limit=0: rows)
    assert sl.learning_report()["india"][0]["frozen"] is True


def test_empty_report_shape():
    rep = sl.learning_report()
    assert set(rep) == {"generated_at", "epoch", "india", "crypto", "ladder"}
    assert isinstance(rep["india"], list) and isinstance(rep["crypto"], list)
