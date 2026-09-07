import asyncio

import pytest

from index_ai.reconcile import (
    GHOST_JOURNAL,
    ORPHAN_BROKER,
    OVER_FILLED,
    UNDER_FILLED,
    diff_positions,
    expected_positions,
    reconcile,
)
from index_ai.scan_health import (
    FAILURES_TO_TRIP,
    ScanHealth,
    gather_limited,
    run_stage,
)


def test_stage_failure_is_isolated_not_raised():
    async def main():
        h = ScanHealth()

        async def boom():
            raise RuntimeError("stage exploded")

        ok, val = await run_stage(h, "boom", boom)
        assert ok is False and val is None
        assert h.stage("boom").failed == 1
        assert "stage exploded" in h.stage("boom").last_error

    asyncio.run(main())


def test_breaker_trips_and_skips_after_repeated_failures():
    async def main():
        h = ScanHealth()
        calls = 0

        async def boom():
            nonlocal calls
            calls += 1
            raise ValueError("nope")

        for _ in range(FAILURES_TO_TRIP):
            await run_stage(h, "s", boom)
        assert calls == FAILURES_TO_TRIP
        await run_stage(h, "s", boom)          # tripped -> not invoked
        assert calls == FAILURES_TO_TRIP
        assert h.stage("s").skipped == 1
        assert "s" in h.as_dict()["tripped"]

    asyncio.run(main())


def test_success_resets_consecutive_failures():
    async def main():
        h = ScanHealth()

        async def boom():
            raise RuntimeError("x")

        async def fine():
            return 1

        await run_stage(h, "s", boom)
        assert h.stage("s").consecutive_failures == 1
        await run_stage(h, "s", fine)
        assert h.stage("s").consecutive_failures == 0
        assert h.stage("s").last_error is None

    asyncio.run(main())


def test_stage_timeout_is_recorded():
    async def main():
        h = ScanHealth()

        async def slow():
            await asyncio.sleep(3)

        ok, _ = await run_stage(h, "slow", slow, timeout=0.02)
        assert ok is False
        assert "Timeout" in (h.stage("slow").last_error or "")

    asyncio.run(main())


def test_gather_limited_caps_concurrency_and_returns_exceptions():
    async def main():
        live = peak = 0

        async def worker():
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return "ok"

        async def bad():
            raise KeyError("k")

        res = await gather_limited([worker for _ in range(8)], limit=2)
        assert res == ["ok"] * 8
        assert peak <= 2, peak

        mixed = await gather_limited([bad, worker], limit=2)
        assert isinstance(mixed[0], KeyError)
        assert mixed[1] == "ok"

    asyncio.run(main())


@pytest.mark.parametrize(
    "expected,actual,sid,kind",
    [
        ({1: 65}, {1: 30}, 1, UNDER_FILLED),
        ({1: 65}, {1: 130}, 1, OVER_FILLED),
        ({1: 65}, {}, 1, GHOST_JOURNAL),
        ({}, {2: 30}, 2, ORPHAN_BROKER),
        ({1: 65}, {1: -65}, 1, ORPHAN_BROKER),
    ],
)
def test_drift_classification(expected, actual, sid, kind):
    issues = {i["security_id"]: i["kind"] for i in diff_positions(expected, actual)}
    assert issues[sid] == kind


def test_matched_positions_report_no_drift():
    assert diff_positions({1: 65, 2: -30}, {1: 65, 2: -30}) == []


def test_expected_positions_signs_sells_negative():
    trades = [{"option": {"legs": [
        {"security_id": 1, "quantity": 65, "transaction_type": "BUY"},
        {"security_id": 2, "quantity": 30, "transaction_type": "SELL"},
    ]}}]
    assert expected_positions(trades) == {1: 65, 2: -30}


def test_reconcile_skips_paper_mode():
    out = reconcile(object(), mode="PAPER")
    assert out["ok"] is True and "skipped" in out
