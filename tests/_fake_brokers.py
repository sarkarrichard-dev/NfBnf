"""Shared broker-replay test helper (D-03).

Replays real, redacted, recorded broker traffic (``tests/fixtures/broker_traffic/``)
through the real client code (``crypto.delta.client.DeltaClient``, and Dhan's
equivalent from plan 02-03) over ``httpx.MockTransport``, with the ability to
inject a fault (a lost reply, a timeout, a race) at a specific call. This
exercises the real signing / retry / error-handling / result-unwrapping code —
only the network hop is faked. No mocking library (``responses``/`respx`/
``pytest-httpx``) is used anywhere in this repo; this module follows the same
plain-classes convention as every other test stub here.

Imported as ``from _fake_brokers import ...`` — pytest's default prepend
import mode puts ``tests/`` on ``sys.path``.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import threading
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "broker_traffic"


def load_feed_frames() -> list[bytes]:
    """Real, redacted Dhan tick-feed frames (``dhan_feed.jsonl``, plan 02-06,
    D-03) decoded back to raw bytes. Missing fixture returns an empty list."""
    return [
        base64.b64decode(row["frame_b64"])
        for row in load_traffic("dhan_feed")
        if row.get("frame_b64")
    ]


def load_traffic(name: str) -> list[dict[str, Any]]:
    """Rows from ``FIXTURES / f"{name}.jsonl"``, oldest-first. Missing file
    (e.g. a broker not yet captured) returns an empty list rather than
    raising — a test can still layer `script()`/`fault()` on top."""
    path = FIXTURES / f"{name}.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


class Block:
    """A fault action for race tests (Task 2 — cancel racing a fill). The
    handler sets `entered` as soon as the blocked request arrives, then waits
    up to 10s for the test to set `release` before answering normally."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()


def _to_response(resp: Any) -> httpx.Response:
    if isinstance(resp, tuple):
        status, body = resp
    else:
        status, body = 200, resp
    return httpx.Response(status, json=body)


class BrokerReplay:
    """Answers requests from (in order of precedence) a pending fault action,
    a scripted response, or the latest matching recorded row — anything else
    raises AssertionError naming the unrecorded call, so an unexpected broker
    call (e.g. a second order) fails the test loudly rather than silently."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.calls: list[tuple[str, str, Any]] = []
        self._scripted: dict[tuple[str, str], list[Any]] = {}
        self._faults: dict[tuple[str, str], list[Any]] = {}

    def script(self, method: str, path: str, *responses: Any) -> None:
        """Each response is a body, or a (status, body) tuple. The last
        scripted response keeps answering once the others are consumed."""
        self._scripted[(method.upper(), path)] = list(responses)

    def fault(self, method: str, path: str, *actions: Any) -> None:
        """Each action is an exception class/instance (httpx transport
        exceptions get `request` attached automatically) or a `Block`."""
        key = (method.upper(), path)
        self._faults.setdefault(key, [])
        self._faults[key].extend(actions)

    def count(self, method: str, path_prefix: str) -> int:
        m = method.upper()
        return sum(1 for cm, cp, _ in self.calls if cm == m and cp.startswith(path_prefix))

    def _latest_row(self, method: str, path: str) -> dict[str, Any] | None:
        for row in reversed(self._rows):
            if str(row.get("method", "")).upper() == method and row.get("path") == path:
                return row
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        try:
            body = json.loads(request.content) if request.content else None
        except ValueError:
            body = None
        self.calls.append((method, path, body))
        key = (method, path)

        faults = self._faults.get(key)
        if faults:
            action = faults.pop(0)
            if isinstance(action, Block):
                action.entered.set()
                action.release.wait(timeout=10.0)
                # falls through to a normal (scripted/recorded) answer below
            elif isinstance(action, type) and issubclass(action, BaseException):
                raise action("simulated fault", request=request)
            elif isinstance(action, BaseException):
                try:
                    action.request = request
                except Exception:
                    pass
                raise action
            else:
                raise AssertionError(f"unsupported fault action: {action!r}")

        scripted = self._scripted.get(key)
        if scripted:
            resp = scripted[0] if len(scripted) == 1 else scripted.pop(0)
            return _to_response(resp)

        row = self._latest_row(method, path)
        if row is not None:
            return httpx.Response(int(row.get("status") or 200), json=row.get("body"))

        raise AssertionError(f"unrecorded broker call: {method} {path}")


class _NoSleepTime:
    """Stand-in for the `time` module reference inside crypto.delta.client —
    `time()` (used for the signature timestamp) passes through untouched;
    only `sleep()` (429/GET-retry back-off) is a no-op, so a replayed retry
    doesn't actually pause the test."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def sleep(self, *_a: Any, **_k: Any) -> None:
        return None


def fake_dhan_client(replay: BrokerReplay, monkeypatch: Any = None) -> Any:
    """A real `DhanClient` wired to `replay` via `httpx.MockTransport` — every
    retry/error/result-unwrapping code path is the real one, only the network
    hop is faked."""
    from index_ai.config import DhanSettings
    from index_ai.dhan import DhanClient

    settings = DhanSettings(
        client_id="1000000001",
        access_token="test-token",
        api_base_url="https://api.dhan.co/v2",
        api_key="",
        api_secret="",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    client = DhanClient(settings, transport=httpx.MockTransport(replay.handler))
    if monkeypatch is not None:
        import index_ai.dhan as _dhan_mod
        import index_ai.dhan_auth as _dhan_auth_mod

        monkeypatch.setattr(_dhan_auth_mod, "auto_refresh_dhan_token", lambda *a, **k: {})
        monkeypatch.setattr(_dhan_mod._limiter, "wait", lambda: None)
        monkeypatch.setattr(_dhan_mod, "time", _NoSleepTime(_dhan_mod.time))
    return client


def fake_delta_client(replay: BrokerReplay, monkeypatch: Any = None) -> Any:
    """A real `DeltaClient` wired to `replay` via `httpx.MockTransport` — every
    signing/retry/error/result-unwrapping code path is the real one, only the
    network hop is faked. Rows whose `source` is not "captured" are allowed;
    the replay never writes files."""
    from crypto.config import crypto_settings
    from crypto.delta.client import DeltaClient

    settings = dataclasses.replace(crypto_settings(), api_key="test-key", api_secret="test-secret")
    client = DeltaClient(settings, transport=httpx.MockTransport(replay.handler))
    if monkeypatch is not None:
        import crypto.delta.client as _client_mod

        monkeypatch.setattr(_client_mod, "time", _NoSleepTime(_client_mod.time))
    return client


# --- shared live-lane test setup (plan 02-02 reuses this without editing) ----

# Fixed so a test can compute the exact `_coid(...)`-derived client_order_id
# ahead of time and script a matching order-history row.
FAKE_ENTRY_TS = "2026-09-07T18:05:00+00:00"


def setup_live_crypto_lane(
    monkeypatch: Any, tmp_path: Path, *, enter_event: dict[str, Any]
) -> None:
    """Env, state/journal paths, products, market data, charges, session
    gates, crypto_live_pairs, executor.live_gate and executor.reconcile, ML
    gate — everything `test_crypto_phase4.live_lane` stubs, minus
    `place_entry`/`settle_entry`/`fill_report`/`position_state`, which run for
    real against whatever `fake_delta_client(...)` is installed as the scan's
    client. The strategy's own step is stubbed to emit exactly one "enter"
    event at `FAKE_ENTRY_TS` (the strategy logic itself is not under test
    here) — `enter_event` is filled in with `fired`/`ts`/`sym`/`side` once
    that happens, so a test can assert on it without inspecting `lanes`.
    """
    import pandas as pd

    from crypto import journal, lanes
    from crypto.delta.products import Contract

    monkeypatch.setenv("CRYPTO_TRADING_MODE", "LIVE")
    monkeypatch.setenv("CRYPTO_ALLOW_LIVE", "true")
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTCUSD")
    monkeypatch.setenv("DELTA_API_KEY", "test-key")
    monkeypatch.setenv("DELTA_API_SECRET", "test-secret")
    monkeypatch.setenv("CRYPTO_NY_NBREAK_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_NBREAK_ALLROUND", "false")
    monkeypatch.setenv("CRYPTO_ICHIMOKU_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_AK_ROXX_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_CPR_TREND_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_RSI_ADX_TREND_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_USDINR", "88")

    monkeypatch.setattr(journal, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "journal.jsonl")

    btc = Contract("BTCUSD", 27, 0.001, 0.5, 1, 100)
    monkeypatch.setattr(lanes.products, "all_contracts", lambda client=None: {"BTCUSD": btc})

    frame = pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-09-07 17:30", periods=30, freq="5min", tz="Asia/Kolkata"
            ),
            "open": [63000.0] * 30,
            "high": [63100.0] * 30,
            "low": [62900.0] * 30,
            "close": [63000.0] * 30,
            "volume": [10.0] * 30,
        }
    )
    monkeypatch.setattr(lanes.market_data, "candles", lambda sym, res, **k: frame)
    monkeypatch.setattr(lanes.market_data, "depth", lambda *a, **k: {})
    monkeypatch.setattr(lanes.market_data, "ticker", lambda *a, **k: {"mark_price": 63000.0})
    monkeypatch.setattr(lanes.charges, "sample_spread", lambda *a, **k: None)
    monkeypatch.setattr(lanes.charges, "sample_funding_rate", lambda *a, **k: None)
    monkeypatch.setattr(lanes, "in_ny_window", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "in_crypto_session", lambda *a, **k: True)
    monkeypatch.setattr(lanes, "ny_session_date", lambda *a, **k: "2026-09-07")
    monkeypatch.setattr(lanes, "_live_wallet_usd", lambda c: 5000.0)
    monkeypatch.setattr(lanes.executor, "reconcile", lambda c: [])
    monkeypatch.setattr(lanes.executor, "live_gate", lambda s=None: (True, ""))
    monkeypatch.setattr(lanes.ml_gate, "check", lambda *a, **k: {"allowed": True, "reason": ""})
    monkeypatch.setattr(
        "index_ai.strategy_performance.crypto_live_pairs", lambda: {("ny_n_break", "BTCUSD")}
    )

    def _fake_step(
        sym,
        c5,
        c15=None,
        *,
        state=None,
        cfg=None,
        in_session=True,
        session_date=None,
        live_price=None,
        **_kw,
    ):
        if state is not None:  # already entered once — nothing more to do
            return state, {"strategy": "ny_n_break", "asset": sym, "event": "hold"}
        ev = {
            "strategy": "ny_n_break",
            "asset": sym,
            "event": "enter",
            "side": "long",
            "price": 63000.0,
            "reason": "test signal (strategy logic not under test here)",
            "ts": FAKE_ENTRY_TS,
        }
        enter_event.update(fired=True, ts=FAKE_ENTRY_TS, sym=sym, side="long")
        return {"position": None}, ev

    monkeypatch.setattr(lanes.nb, "step", _fake_step)


# --- Dhan tick-feed fault injection (plan 02-06, D-04) -----------------------


class FakeDhanFeed:
    """Fake replacement for ``websockets.connect`` in ``tick_feed`` tests.

    ``sessions`` is a list of per-connection item lists; each ``connect()``
    call's ``__aenter__`` consumes the next session in order (recorded, with
    an optional shared ``events`` list, so a test can compare connection
    order against flush timing). An item is:

    - ``bytes``/``bytearray``: returned by ``recv()``
    - an exception instance or class: raised by ``recv()``
    - ``FakeDhanFeed.WAIT``: ``recv()`` awaits forever (until cancelled —
      e.g. by the caller's own ``asyncio.wait_for`` stall timeout)
    - any other callable: called with no args (e.g. ``stop.set``), then
      ``recv()`` moves on to the next item in the same call

    A session that runs out of items raises
    ``websockets.exceptions.ConnectionClosedOK(None, None)`` — ending a
    session right after a callable that sets ``stop`` makes ``run_feed``
    return promptly.
    """

    WAIT = object()

    def __init__(self, sessions: list[list[Any]], events: list[tuple] | None = None) -> None:
        self._sessions = list(sessions)
        self._next_session = 0
        self.events: list[tuple] = events if events is not None else []
        self.connections: list["_FakeFeedConnection"] = []

    def connect(self, url: str, **kwargs: Any) -> "_FakeFeedConnection":
        return _FakeFeedConnection(self)


class _FakeFeedConnection:
    def __init__(self, feed: FakeDhanFeed) -> None:
        self._feed = feed
        self._session: list[Any] = []
        self._pos = 0
        self.sent: list[str] = []

    async def __aenter__(self) -> "_FakeFeedConnection":
        idx = self._feed._next_session
        self._feed._next_session += 1
        self._session = self._feed._sessions[idx]
        self._feed.events.append(("connect", idx))
        self._feed.connections.append(self)
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes:
        import asyncio

        import websockets.exceptions

        while True:
            if self._pos >= len(self._session):
                raise websockets.exceptions.ConnectionClosedOK(None, None)
            item = self._session[self._pos]
            self._pos += 1
            if isinstance(item, (bytes, bytearray)):
                return bytes(item)
            if item is FakeDhanFeed.WAIT:
                await asyncio.Event().wait()  # never set — waits until cancelled
                continue
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, type) and issubclass(item, BaseException):
                raise item()
            if callable(item):
                item()
                continue
            raise AssertionError(f"unsupported FakeDhanFeed session item: {item!r}")
