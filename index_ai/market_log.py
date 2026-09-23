"""
Time-series log of what the system saw and what it decided, in SQLite.

Three tables, kept in their own database so the trade journal stays small:

  ``observations``  what the market looked like at a moment — spot, ATM IV, PCR,
                    max pain, call/put OI, measured spreads, VIX, the signal
                    direction and regime read.
  ``decisions``     every entry evaluation and its outcome, including the ones
                    that did NOT trade and why.

The second table is the one that makes retrospect possible. Today a skipped
setup leaves nothing behind except a line in an 80-item in-memory deque, so
"why didn't it trade at 10:15?" is unanswerable an hour later. Persisting the
refusals is what lets you later ask whether the filters were right — a filter
that blocks winners is invisible unless you record what it blocked.

  ``ticks``         real exchange ticks from Dhan's websocket feed, written in
                    batches by ``tick_feed`` (opt-in via ENABLE_TICK_FEED).

Two resolutions, deliberately: ``observations`` is one row per instrument per
scan cycle and always available; ``ticks`` is per exchange update and only when
the websocket is running. ``stats()["resolution"]`` reports which you actually
have, so nothing infers tick data that was never collected.

  ``chain``         real option-chain snapshots — per strike, CE and PE: last
                    price, best bid/ask, OI, volume, IV and greeks — saved from
                    the chain the planner already downloads (no extra Dhan call),
                    at most once a minute per index. This is the real-price
                    record new option strategies get tested against, instead of
                    a Black-Scholes guess (there is no historical option chain).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist, today_ist_date

DB_PATH = MEMORY_DIR / "market_log.sqlite"
RETENTION_DAYS = 400


def enabled() -> bool:
    return os.getenv("ENABLE_MARKET_LOG", "true").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        # WAL so a long read (a backtest over the log) never blocks the scanner's writes
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        _migrate(db)
        yield db
        db.commit()
    finally:
        db.close()


def _migrate(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            session TEXT NOT NULL,
            instrument TEXT NOT NULL,
            spot REAL,
            atm_iv REAL,
            pcr REAL,
            max_pain REAL,
            ce_oi REAL,
            pe_oi REAL,
            near_half_spread REAL,
            wing_half_spread REAL,
            vix REAL,
            regime TEXT,
            signal_direction TEXT,
            extra TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            session TEXT NOT NULL,
            instrument TEXT NOT NULL,
            lane TEXT NOT NULL,
            event TEXT NOT NULL,
            traded INTEGER NOT NULL,
            reason TEXT,
            win_probability REAL,
            extra TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            session TEXT NOT NULL,
            instrument TEXT,
            security_id INTEGER NOT NULL,
            exchange_segment INTEGER,
            kind TEXT NOT NULL,
            ltp REAL,
            ltq INTEGER,
            ltt INTEGER,
            atp REAL,
            volume INTEGER,
            buy_qty INTEGER,
            sell_qty INTEGER,
            oi INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            session TEXT NOT NULL,
            instrument TEXT NOT NULL,
            expiry TEXT,
            spot REAL,
            strike REAL NOT NULL,
            opt_type TEXT NOT NULL,
            security_id INTEGER,
            ltp REAL,
            bid REAL,
            ask REAL,
            bid_qty REAL,
            ask_qty REAL,
            oi REAL,
            prev_oi REAL,
            volume REAL,
            iv REAL,
            delta REAL,
            gamma REAL,
            theta REAL,
            vega REAL
        )
    """)
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session, instrument)",
        "CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts)",
        "CREATE INDEX IF NOT EXISTS idx_dec_session ON decisions(session, instrument, lane)",
        "CREATE INDEX IF NOT EXISTS idx_dec_traded ON decisions(traded, session)",
        "CREATE INDEX IF NOT EXISTS idx_tick_session ON ticks(session, instrument)",
        "CREATE INDEX IF NOT EXISTS idx_tick_ltt ON ticks(security_id, ltt)",
        "CREATE INDEX IF NOT EXISTS idx_chain_session ON chain(session, instrument, ts)",
    ):
        db.execute(stmt)


def _f(v: Any) -> float | None:
    try:
        out = float(v)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


def record_observation(instrument: str, **fields: Any) -> None:
    """One market snapshot. Never raises — logging must not break a trading tick."""
    if not enabled():
        return
    known = ("spot", "atm_iv", "pcr", "max_pain", "ce_oi", "pe_oi",
             "near_half_spread", "wing_half_spread", "vix")
    row = {k: _f(fields.get(k)) for k in known}
    extra = {k: v for k, v in fields.items()
             if k not in known and k not in ("regime", "signal_direction")}
    try:
        with connect() as db:
            db.execute(
                f"""INSERT INTO observations
                    (ts, session, instrument, {", ".join(known)},
                     regime, signal_direction, extra)
                    VALUES (?,?,?,{",".join("?" * len(known))},?,?,?)""",
                (now_ist().isoformat(timespec="seconds"), today_ist_date(),
                 str(instrument).upper(), *[row[k] for k in known],
                 fields.get("regime"), fields.get("signal_direction"),
                 json.dumps(extra, default=str) if extra else None),
            )
    except Exception:
        pass


def record_decision(instrument: str, lane: str, event: str, *, traded: bool,
                    reason: str | None = None, win_probability: float | None = None,
                    **extra: Any) -> None:
    """Every entry evaluation, including refusals — this is the retrospect table."""
    if not enabled():
        return
    try:
        with connect() as db:
            db.execute(
                """INSERT INTO decisions
                   (ts, session, instrument, lane, event, traded, reason, win_probability, extra)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (now_ist().isoformat(timespec="seconds"), today_ist_date(),
                 str(instrument).upper(), str(lane), str(event), 1 if traded else 0,
                 (str(reason)[:500] if reason else None), _f(win_probability),
                 json.dumps(extra, default=str) if extra else None),
            )
    except Exception:
        pass


def in_background(fn, *args: Any, **kwargs: Any) -> None:
    """Run a recorder off the caller's thread. The scanner calls these from
    inside the asyncio loop, and a sync SQLite write there stalls every
    dashboard poll. Recorders never raise, so fire-and-forget is safe."""
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


CHAIN_STRIKES_EACH_SIDE = 15   # ±15 strikes around spot, CE + PE = 62 rows a snapshot
CHAIN_MIN_GAP_SECONDS = 55     # scanner fetches every 90s; dashboard calls must not add duplicates
_last_chain_at: dict[tuple[str, str | None], float] = {}
_CHAIN_COLS = ("security_id", "ltp", "bid", "ask", "bid_qty", "ask_qty", "oi",
               "prev_oi", "volume", "iv", "delta", "gamma", "theta", "vega")


def _chain_leg(leg: dict[str, Any]) -> tuple:
    g = leg.get("greeks") or {}
    return (
        _f(leg.get("security_id")), _f(leg.get("last_price")),
        _f(leg.get("top_bid_price")), _f(leg.get("top_ask_price")),
        _f(leg.get("top_bid_quantity")), _f(leg.get("top_ask_quantity")),
        _f(leg.get("oi")), _f(leg.get("previous_oi")), _f(leg.get("volume")),
        _f(leg.get("implied_volatility")),
        _f(g.get("delta")), _f(g.get("gamma")), _f(g.get("theta")), _f(g.get("vega")),
    )


def chain_due(instrument: str, expiry: str | None) -> bool:
    """Would a snapshot for this index + expiry be saved now? Lets a caller
    skip an extra Dhan request whose answer would be thrown away."""
    key = (str(instrument).upper(), expiry)
    return enabled() and time.monotonic() - _last_chain_at.get(key, -1e9) >= CHAIN_MIN_GAP_SECONDS


def record_chain_snapshot(instrument: str, expiry: str | None, spot: float,
                          chain: dict[str, Any]) -> int:
    """Save the strikes nearest spot from one Dhan option-chain response.
    Throttled per (index, expiry); returns rows written. Never raises."""
    if not chain_due(instrument, expiry):
        return 0
    key = str(instrument).upper()
    _last_chain_at[(key, expiry)] = time.monotonic()
    try:
        oc = (chain.get("data") or {}).get("oc") or {}
        strikes = sorted((float(k), v) for k, v in oc.items() if _f(k) is not None)
        strikes.sort(key=lambda kv: abs(kv[0] - float(spot)))
        ts, session = now_ist().isoformat(timespec="seconds"), today_ist_date()
        rows = [
            (ts, session, key, expiry, _f(spot), strike, side.upper(), *_chain_leg(leg))
            for strike, row in strikes[: 2 * CHAIN_STRIKES_EACH_SIDE + 1]
            for side in ("ce", "pe")
            if isinstance(leg := (row or {}).get(side), dict) and leg
        ]
        if not rows:
            return 0
        with connect() as db:
            db.executemany(
                f"""INSERT INTO chain (ts, session, instrument, expiry, spot, strike,
                    opt_type, {", ".join(_CHAIN_COLS)})
                    VALUES ({",".join("?" * (7 + len(_CHAIN_COLS)))})""",
                rows,
            )
        return len(rows)
    except Exception:
        return 0


_TICK_COLS = ("kind", "ltp", "ltq", "ltt", "atp", "volume",
              "buy_qty", "sell_qty", "oi", "open", "high", "low", "close")


def record_tick_batch(packets: list[dict[str, Any]], sec_map: dict[int, str] | None = None) -> int:
    """Insert a batch of decoded websocket packets. Returns rows written.

    Batched in one transaction: a liquid index option prints hundreds of ticks a
    second, and an INSERT per tick would make the writer the bottleneck.
    """
    if not enabled() or not packets:
        return 0
    smap = sec_map or {}
    ts, session = now_ist().isoformat(timespec="seconds"), today_ist_date()
    rows = []
    for p in packets:
        sid = p.get("security_id")
        if sid is None:
            continue
        rows.append((
            ts, session, smap.get(int(sid)), int(sid), p.get("exchange_segment"),
            str(p.get("type") or "tick"),
            _f(p.get("ltp")), p.get("ltq"), p.get("ltt"), _f(p.get("atp")),
            p.get("volume"), p.get("total_buy_quantity"), p.get("total_sell_quantity"),
            p.get("oi"), _f(p.get("open")), _f(p.get("high")), _f(p.get("low")), _f(p.get("close")),
        ))
    if not rows:
        return 0
    try:
        with connect() as db:
            db.executemany(
                f"""INSERT INTO ticks
                    (ts, session, instrument, security_id, exchange_segment, {", ".join(_TICK_COLS)})
                    VALUES ({",".join("?" * (5 + len(_TICK_COLS)))})""",
                rows,
            )
        return len(rows)
    except Exception:
        return 0


def ticks(session: str | None = None, instrument: str | None = None,
          limit: int = 500) -> list[dict[str, Any]]:
    try:
        with connect() as db:
            q, args = "SELECT * FROM ticks WHERE 1=1", []
            if session:
                q += " AND session=?"
                args.append(session)
            if instrument:
                q += " AND instrument=?"
                args.append(str(instrument).upper())
            q += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in db.execute(q, args).fetchall()]
    except Exception:
        return []


def skip_reasons(session: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Most common reasons a lane did NOT trade — where the filters actually bite."""
    try:
        with connect() as db:
            if session:
                rows = db.execute(
                    """SELECT instrument, lane, reason, COUNT(*) n FROM decisions
                       WHERE traded=0 AND session=? GROUP BY instrument, lane, reason
                       ORDER BY n DESC LIMIT ?""", (session, limit)).fetchall()
            else:
                rows = db.execute(
                    """SELECT instrument, lane, reason, COUNT(*) n FROM decisions
                       WHERE traded=0 GROUP BY instrument, lane, reason
                       ORDER BY n DESC LIMIT ?""", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def observations(session: str | None = None, instrument: str | None = None,
                 limit: int = 500) -> list[dict[str, Any]]:
    try:
        with connect() as db:
            q = "SELECT * FROM observations WHERE 1=1"
            args: list[Any] = []
            if session:
                q += " AND session=?"
                args.append(session)
            if instrument:
                q += " AND instrument=?"
                args.append(str(instrument).upper())
            q += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in db.execute(q, args).fetchall()]
    except Exception:
        return []


def stats() -> dict[str, Any]:
    try:
        with connect() as db:
            obs = db.execute("SELECT COUNT(*) c, MIN(session) a, MAX(session) b FROM observations").fetchone()
            dec = db.execute("SELECT COUNT(*) c, SUM(traded) t FROM decisions").fetchone()
            tk = db.execute("SELECT COUNT(*) c, MAX(session) s FROM ticks").fetchone()
            ch = db.execute("SELECT COUNT(*) c, MIN(session) a, MAX(session) b FROM chain").fetchone()
            size = DB_PATH.stat().st_size if DB_PATH.is_file() else 0
            return {
                "enabled": enabled(),
                "observations": obs["c"], "first_session": obs["a"], "last_session": obs["b"],
                "decisions": dec["c"], "traded": dec["t"] or 0,
                "skipped": (dec["c"] or 0) - (dec["t"] or 0),
                "ticks": tk["c"], "last_tick_session": tk["s"],
                "chain_rows": ch["c"], "chain_first_session": ch["a"],
                "chain_last_session": ch["b"],
                "db_mb": round(size / 1e6, 2),
                "resolution": (
                    "exchange ticks (websocket) + scan-cycle observations"
                    if tk["c"] else "scan cycle (REST polling) — enable ENABLE_TICK_FEED for ticks"
                ),
            }
    except Exception as exc:
        return {"enabled": enabled(), "error": str(exc)[:200]}


def prune(days: int = RETENTION_DAYS) -> int:
    """Drop rows older than ``days`` sessions. Returns rows removed."""
    from datetime import timedelta

    cutoff = (now_ist() - timedelta(days=days)).date().isoformat()
    removed = 0
    try:
        with connect() as db:
            for table in ("observations", "decisions", "ticks", "chain"):
                cur = db.execute(f"DELETE FROM {table} WHERE session < ?", (cutoff,))
                removed += cur.rowcount or 0
    except Exception:
        return 0
    return removed


if __name__ == "__main__":  # ponytail self-check
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        globals()["DB_PATH"] = Path(td) / "m.sqlite"
        os.environ["ENABLE_MARKET_LOG"] = "true"
        record_observation("NIFTY", spot=24175.65, atm_iv=10.3, pcr=0.92,
                           max_pain=24200, near_half_spread=0.2, wing_half_spread=0.6,
                           vix=10.66, regime="TREND", signal_direction="LONG",
                           note="weekend smoke")
        record_decision("NIFTY", "sell", "entry", traded=True, reason="clean breakout",
                        win_probability=0.61)
        record_decision("BANKNIFTY", "sell", "skip", traded=False,
                        reason="NOT_VIABLE: cannot cover floor")
        record_decision("BANKNIFTY", "sell", "skip", traded=False,
                        reason="NOT_VIABLE: cannot cover floor")

        s = stats()
        assert s["observations"] == 1 and s["decisions"] == 3 and s["traded"] == 1
        assert s["skipped"] == 2

        obs = observations(instrument="NIFTY")
        assert obs and obs[0]["spot"] == 24175.65 and obs[0]["regime"] == "TREND"
        assert json.loads(obs[0]["extra"])["note"] == "weekend smoke"

        top = skip_reasons()
        assert top[0]["n"] == 2 and "NOT_VIABLE" in top[0]["reason"]

        # disabled -> silent no-op, and a broken value must not raise
        os.environ["ENABLE_MARKET_LOG"] = "false"
        record_observation("NIFTY", spot="not-a-number")
        assert stats()["observations"] == 1
        os.environ["ENABLE_MARKET_LOG"] = "true"
        record_observation("NIFTY", spot="not-a-number")   # coerces to NULL, no raise
        assert stats()["observations"] == 2

        # chain snapshot: nearest strikes only, both sides, throttled per index
        oc = {f"{24000 + 50 * i}.000000": {
            "ce": {"last_price": 100 - i, "top_bid_price": 99, "top_ask_price": 101,
                   "oi": 1000, "security_id": 5000 + i, "greeks": {"delta": 0.5}},
            "pe": {"last_price": 90 + i, "oi": 2000}} for i in range(-40, 41)}
        assert record_chain_snapshot("NIFTY", "2026-09-29", 24010, {"data": {"oc": oc}}) == 62
        assert record_chain_snapshot("NIFTY", "2026-09-29", 24010, {"data": {"oc": oc}}) == 0
        assert record_chain_snapshot("SENSEX", None, 1, {"data": {}}) == 0
        with connect() as db:
            r = db.execute("SELECT MIN(strike) lo, MAX(strike) hi FROM chain").fetchone()
            ce = db.execute("SELECT * FROM chain WHERE strike=24000 AND opt_type='CE'").fetchone()
        assert (r["lo"], r["hi"]) == (23250, 24750)
        assert ce["bid"] == 99 and ce["delta"] == 0.5 and ce["security_id"] == 5000
        assert stats()["chain_rows"] == 62
        print("market_log.py self-check ok")
