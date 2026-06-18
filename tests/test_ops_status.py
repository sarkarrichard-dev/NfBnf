from __future__ import annotations

from index_ai.ops_status import build_ops_status, preview_scan


def test_build_ops_status_shape() -> None:
    data = build_ops_status()
    assert "blockers" in data
    assert "can_enter_trades" in data
    assert "scanner" in data
    assert "events" in data
    assert isinstance(data["blockers"], list)
