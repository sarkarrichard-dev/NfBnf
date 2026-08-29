"""
Per-stage isolation, timing and circuit breaking for the scanner loop.

The scan cycle is a chain of independent stages (trail checks, square-off, each
lane's paper tick, each index scan). Before this, one stage raising aborted the
whole cycle, so a single flaky lane could stop trailing stops from being checked.

``run_stage`` isolates each stage, times it, and trips a breaker on a stage that
keeps failing so a persistently broken step is skipped for a cooldown instead of
burning the cycle budget every 90 seconds. Health is exposed for the dashboard.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

FAILURES_TO_TRIP = 3
COOLDOWN_SECONDS = 300.0
DEFAULT_TIMEOUT = 60.0


@dataclass
class StageHealth:
    name: str
    ok: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_ms: float = 0.0
    avg_ms: float = 0.0
    tripped_until: float = 0.0
    skipped: int = 0

    def as_dict(self, *, now: float | None = None) -> dict[str, Any]:
        t = now if now is not None else time.monotonic()
        return {
            "name": self.name,
            "ok": self.ok,
            "failed": self.failed,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_ms": round(self.last_ms),
            "avg_ms": round(self.avg_ms),
            "skipped": self.skipped,
            "tripped": self.tripped_until > t,
            "cooldown_remaining_s": max(0, round(self.tripped_until - t)),
        }


@dataclass
class ScanHealth:
    stages: dict[str, StageHealth] = field(default_factory=dict)
    last_cycle_ms: float = 0.0

    def stage(self, name: str) -> StageHealth:
        return self.stages.setdefault(name, StageHealth(name))

    def as_dict(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "last_cycle_ms": round(self.last_cycle_ms),
            "stages": [s.as_dict(now=now) for s in self.stages.values()],
            "tripped": [s.name for s in self.stages.values() if s.tripped_until > now],
        }


async def run_stage(
    health: ScanHealth,
    name: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> tuple[bool, Any]:
    """Run one stage isolated + timed. Returns (ran_ok, result).

    Never raises: a stage failure is recorded and the cycle continues. After
    ``FAILURES_TO_TRIP`` consecutive failures the stage is skipped for
    ``COOLDOWN_SECONDS`` so a broken step stops consuming the cycle.
    """
    st = health.stage(name)
    now = time.monotonic()
    if st.tripped_until > now:
        st.skipped += 1
        return False, None

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(coro_factory(), timeout=timeout)
    except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001 — isolation is the point
        st.failed += 1
        st.consecutive_failures += 1
        st.last_error = f"{type(exc).__name__}: {exc}"[:300]
        st.last_ms = (time.monotonic() - started) * 1000
        if st.consecutive_failures >= FAILURES_TO_TRIP:
            st.tripped_until = time.monotonic() + COOLDOWN_SECONDS
        if on_error is not None:
            try:
                on_error(name, exc)
            except Exception:
                pass
        return False, None

    st.ok += 1
    st.consecutive_failures = 0
    st.last_error = None
    st.last_ms = (time.monotonic() - started) * 1000
    st.avg_ms = st.last_ms if st.avg_ms == 0 else (0.8 * st.avg_ms + 0.2 * st.last_ms)
    return True, result


async def gather_limited(
    factories: list[Callable[[], Awaitable[Any]]], *, limit: int
) -> list[Any]:
    """Run coroutines concurrently, at most ``limit`` in flight (broker rate limits).

    Exceptions are returned in place, never raised — the caller decides.
    """
    sem = asyncio.Semaphore(max(1, limit))

    async def _one(f: Callable[[], Awaitable[Any]]) -> Any:
        async with sem:
            try:
                return await f()
            except Exception as exc:  # noqa: BLE001
                return exc

    return await asyncio.gather(*(_one(f) for f in factories))


if __name__ == "__main__":  # ponytail self-check
    async def main() -> None:
        h = ScanHealth()

        async def good():
            return 42

        async def bad():
            raise RuntimeError("boom")

        ok, val = await run_stage(h, "good", good)
        assert ok and val == 42

        for _ in range(FAILURES_TO_TRIP):
            ok, _ = await run_stage(h, "bad", bad)
            assert not ok
        assert h.stage("bad").tripped_until > time.monotonic()
        ok, _ = await run_stage(h, "bad", bad)          # now skipped, not run
        assert not ok and h.stage("bad").skipped == 1

        async def slow():
            await asyncio.sleep(5)

        ok, _ = await run_stage(h, "slow", slow, timeout=0.05)
        assert not ok and "Timeout" in (h.stage("slow").last_error or "")

        # concurrency is genuinely capped
        live, peak = 0, 0

        async def tracked():
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return "x"

        res = await gather_limited([tracked for _ in range(10)], limit=3)
        assert res == ["x"] * 10 and peak <= 3, peak

        out = await gather_limited([bad, good], limit=2)
        assert isinstance(out[0], RuntimeError) and out[1] == 42

        d = h.as_dict()
        assert "bad" in d["tripped"] and any(s["name"] == "good" for s in d["stages"])
        print("scan_health.py self-check ok")

    asyncio.run(main())
