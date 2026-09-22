"""Crypto lane — both strategies on BTC/ETH perps: size, journal, alert.

``scan_crypto_paper()`` is called from the scanner's crypto task (~60 s). It
never raises: one asset or strategy failing must not stop the others. Paper is
the default; a real Delta order is placed only when ``live_orders_enabled`` is
true (LIVE mode + armed + credentials) — see ``crypto.executor``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto import charges, executor, journal
from index_ai import notify
from crypto.charges import round_trip_cost_usd
from crypto.config import crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import DeltaClient
from crypto.ml import gate as ml_gate
from crypto.session import crypto_day, in_crypto_session, in_ny_window, ny_session_date
from crypto.sizing import lots_from_margin_budget, size_position
from crypto.strategies import cpr_trend
from crypto.strategies import ichimoku as ichi
from crypto.strategies import ny_n_break as nb
from crypto.strategies.trailing import TrailConfig, bracket_stop_price

logger = logging.getLogger(__name__)

# ponytail: one coarse lock around "read crypto_state.json -> mutate -> write
# it back (+ journal)" for the whole section, not a per-key one. The only two
# writers are one ~60s scan cycle and an occasional manual "Close" click from
# the dashboard, so serializing a whole cycle against a whole manual close is
# cheap and closes the lost-update / double-journal race outright: a manual
# close is now atomic with respect to the scan loop, not just with itself.
# Upgrade to per-key locking only if a real throughput need shows up.
_STATE_LOCK = threading.Lock()

_ICHI_DAYS = {"15m": 4, "30m": 8, "1h": 15, "2h": 25, "4h": 45, "6h": 60, "1d": 260}

# Every scan cycle's events (enter/exit/wait/error), most-recent-first, so a
# skipped LIVE entry (insufficient wallet, IP block, ML gate, ...) is visible
# on the dashboard even though it's too routine to log to server.log the way
# an enter/exit is. Mirrors index_ai.scanner's own _state.events deque.
_recent_events: deque[dict[str, Any]] = deque(maxlen=60)


def recent_events() -> list[dict[str, Any]]:
    return list(_recent_events)


def _record_events(events: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for ev in events:
        _recent_events.appendleft({"at": now, **ev})


def _plain_reason(raw: str) -> str:
    """The internal reason strings (sizing math, ML gate scores, broker
    error text) are meant for logs, not for reading at a glance. Translate
    the common ones; an unrecognised reason still shows verbatim rather
    than being hidden."""
    low = raw.lower()
    if "ip_not_whitelisted" in low:
        return "Blocked — your internet address isn't approved on Delta right now"
    if low.startswith("sizing:") and "safe bankroll is $0" in low:
        return "Not enough money in the Delta account to open even the smallest position"
    if low.startswith("sizing:") and "safe bankroll is" in low:
        return f"Not enough money in the Delta account ({raw.split(':', 1)[1].strip()})"
    if low.startswith("sizing:") and "over the" in low and "cap" in low:
        return "Would need more money than your per-trade limit allows"
    if low.startswith("sizing:"):
        return f"Couldn't size the trade ({raw.split(':', 1)[1].strip()})"
    if low.startswith("ml gate:"):
        return f"The AI confidence check said no ({raw.split(':', 1)[1].strip()})"
    if "kill switch" in low:
        return f"Live trading paused — {raw.split(':', 1)[-1].strip()}"
    if low.startswith("order rejected:"):
        return f"Delta rejected the order ({raw.split(':', 1)[1].strip()})"
    return raw


def _log_blocked_live(strat: str, sym: str, side: str, reason: str) -> None:
    """A LIVE signal fired but never became a position — Richard wants this
    visible in the trade table itself (same row shape, real reason instead
    of a PnL number), not just a transient banner."""
    now = datetime.now(timezone.utc).isoformat()
    journal.log_blocked(
        {
            "strategy": strat,
            "asset": sym,
            "side": side,
            "mode": "live",
            "day": now[:10],
            "opened_at": now,
            "closed_at": now,
            "reason": _plain_reason(reason),
        }
    )


def enabled() -> bool:
    """The section runs whenever a strategy is enabled. PAPER vs LIVE is the
    execution mode; turning every strategy toggle off is the pause switch."""
    s = crypto_settings()
    return bool(_enabled_strategies(s))


def _enabled_strategies(s) -> list[str]:
    out = []
    if s.ny_nbreak_enabled:
        out.append("ny_n_break")
    if s.ichimoku_enabled:
        out.append("ichimoku")
    if s.ak_roxx_enabled:
        out.append("ak_roxx_pro")
    if s.bb_reversal_enabled:
        out.append("bb_reversal")
    if s.ema_jaguar_enabled:
        out.append("ema_jaguar")
    if s.vp_edge_enabled:
        out.append("vp_edge")
    if s.cpr_trend_enabled:
        out.append("cpr_trend")
    if s.rsi_adx_trend_enabled:
        out.append("rsi_adx_trend")
    return out


# Strategies proven on only a subset of the configured symbols — see
# crypto/strategies/RESULTS.md. Everything else in _enabled_strategies()
# trades the full s.symbols list; a name here overrides that.
_STRATEGY_SYMBOLS: dict[str, tuple[str, ...]] = {
    # net-positive on these three, net-negative on PAXG/XRP/BNB (2026-09-15)
    "rsi_adx_trend": ("BTCUSD", "ETHUSD", "SOLUSD"),
}


def _symbols_for(strat: str, s) -> tuple[str, ...]:
    return _STRATEGY_SYMBOLS.get(strat, tuple(s.symbols))


def _closed(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the still-forming last bar so signals fire on closed candles only."""
    return df.iloc[:-1].reset_index(drop=True) if len(df) > 1 else df


def _trail_cfg(s) -> TrailConfig:
    return TrailConfig(
        leverage=s.leverage,
        stop_pnl_pct=s.stop_pnl_pct,
        ratchet_step_pnl_pct=s.ratchet_step_pnl_pct,
        tp_trigger_pnl_pct=s.tp_trigger_pnl_pct,
        peak_trail_pnl_pct=s.peak_trail_pnl_pct,
    )


def _ak_roxx_trail(s) -> TrailConfig:
    """ak_roxx_pro's own, much wider trail (Richard, 2026-09-22) — its channel
    band is the real exit (a faithful port of a real indicator), the shared
    trail is only a disaster backstop underneath it, and at the normal shared
    settings it was firing before the channel band nearly every time. Sized
    off the real channel-band exit history: replaying 1,320 backtest trades
    (crypto.backtest.backtest_simple, 7 symbols, 180 days), losing exits were
    a median -11% P&L, 5th percentile -35%, 1st percentile -57% — so -55%
    sits clear of ~98%+ of ordinary channel exits and only catches the true
    tail (the worst recorded were -66% to -95%)."""
    return TrailConfig(
        leverage=s.leverage,
        stop_pnl_pct=55.0,
        ratchet_step_pnl_pct=15.0,
        tp_trigger_pnl_pct=100.0,
        peak_trail_pnl_pct=15.0,
    )


def _trail_cfg_for(strat: str, s) -> TrailConfig:
    return _ak_roxx_trail(s) if strat == "ak_roxx_pro" else _trail_cfg(s)


def _nb_cfg(s) -> nb.NBreakConfig:
    # around-the-clock covers more hours than the 5h NY window, so the per-period
    # cap gets more room (still tunable via the env var).
    default_cap = "6" if getattr(s, "nbreak_allround", False) else "3"
    return nb.NBreakConfig(
        max_trades_per_session=int(
            os.getenv("CRYPTO_NBREAK_MAX_TRADES", default_cap) or default_cap
        ),
        trail=_trail_cfg(s),
    )


def _ichi_cfg(s) -> ichi.IchimokuConfig:
    return ichi.IchimokuConfig(trail=_trail_cfg(s))


def _cpr_trend_cfg(s) -> cpr_trend.CprTrendConfig:
    return cpr_trend.CprTrendConfig(trail=_trail_cfg(s))


def _tuned(name: str) -> dict:
    """Walk-forward params from crypto/ml/optimize.py, else {} → module defaults."""
    try:
        from crypto.ml.optimize import tuned_params

        return tuned_params(name)
    except Exception:
        return {}


def _bb_cfg(s):
    from crypto.strategies.bb_reversal import BBReversalConfig

    return BBReversalConfig(**_tuned("bb_reversal"), trail=_trail_cfg(s))


def _ema_jaguar_cfg(s):
    from crypto.strategies.ema_jaguar import EmaJaguarConfig

    return EmaJaguarConfig(**_tuned("ema_jaguar"), trail=_trail_cfg(s))


def _vp_edge_cfg(s):
    from crypto.strategies.vp_edge import VpEdgeConfig

    return VpEdgeConfig(**_tuned("vp_edge"), trail=_trail_cfg(s))


def _rsi_adx_trend_cfg(s):
    from crypto.strategies.rsi_adx_trend import RsiAdxTrendConfig

    return RsiAdxTrendConfig(trail=_trail_cfg(s))


def _ak_roxx_cfg(s):
    from crypto.strategies.ak_roxx_pro import AkRoxxConfig

    tuned = _tuned("ak_roxx_pro")
    tf = os.getenv("CRYPTO_AK_ROXX_TF", tuned.get("timeframe", "1h")).strip()
    return AkRoxxConfig(**{**tuned, "timeframe": tf}, trail=_ak_roxx_trail(s))


# name -> builder returning (module, timeframe, days-of-history, cfg)
_SIMPLE: dict[str, "Any"] = {}


def _register_simple() -> None:
    from crypto.strategies import ak_roxx_pro, bb_reversal, ema_jaguar, rsi_adx_trend, vp_edge

    _SIMPLE.update(
        {
            "bb_reversal": lambda s: (bb_reversal, "5m", 2, _bb_cfg(s)),
            "ema_jaguar": lambda s: (ema_jaguar, "5m", 2, _ema_jaguar_cfg(s)),
            "vp_edge": lambda s: (vp_edge, "15m", 6, _vp_edge_cfg(s)),
            "ak_roxx_pro": lambda s: (ak_roxx_pro, _ak_roxx_cfg(s).timeframe, 20, _ak_roxx_cfg(s)),
            "rsi_adx_trend": lambda s: (rsi_adx_trend, "1h", 20, _rsi_adx_trend_cfg(s)),
        }
    )


_register_simple()


def _fx_rate(client: DeltaClient, s) -> float:
    if s.credentials_ready:
        try:
            bal = client.wallet() or []
            usd = sum(float(w.get("balance") or 0) for w in bal if isinstance(w, dict))
            inr = sum(float(w.get("balance_inr") or 0) for w in bal if isinstance(w, dict))
            if usd > 0 and inr > 0:
                return inr / usd
        except Exception:
            logger.debug("crypto fx from wallet failed, using fallback", exc_info=True)
    try:
        return float(os.getenv("CRYPTO_USDINR", "88") or 88)
    except ValueError:
        return 88.0


def _live_mark(sym: str, client: DeltaClient) -> float | None:
    """Current mark price, for the trailing stop/target's per-scan live check
    (a strategy's own signal price is the last closed candle — see
    crypto/strategies/cpr_trend.py). None on any fetch failure; callers fall
    back to the candle close."""
    try:
        return float(market_data.ticker(sym, client=client).get("mark_price") or 0) or None
    except Exception:
        return None


def _open_counts(state: dict[str, Any]) -> dict[str, int]:
    """Open positions per strategy — each strategy trades its own independent book."""
    out: dict[str, int] = defaultdict(int)
    for k, v in state.items():
        if ":" in k and isinstance(v, dict) and v.get("position"):
            out[k.split(":", 1)[0]] += 1
    return out


def _hold_exceeded(pos: dict[str, Any], now_utc: datetime, max_days: int) -> bool:
    """True once a position has been open across more than ``max_days`` UTC-day
    boundaries. Crypto has no session — Richard: opened today may close tomorrow,
    no more."""
    opened = pos.get("opened_at")
    if not opened:
        return False
    try:
        od = datetime.fromisoformat(str(opened)).date()
    except ValueError:
        return False
    return (now_utc.date() - od).days > max_days


def scan_crypto_paper(client: DeltaClient | None = None) -> list[dict[str, Any]]:
    s = crypto_settings()
    if not _enabled_strategies(s):
        return []
    own = client is None
    client = client or DeltaClient(s)
    try:
        with _STATE_LOCK:  # serialize against a concurrent manual close (see lock docstring)
            events = _scan(s, client)
            _record_events(events)
            return events
    except Exception as exc:  # the "never raises" contract — the loop must survive
        logger.warning("crypto scan aborted", exc_info=True)
        err_event = [{"event": "error", "where": "scan", "error": str(exc)}]
        _record_events(err_event)
        return err_event
    finally:
        if own and client is not None:  # close the pooled httpx.Client we opened
            client.close()


def _scan(s, client: DeltaClient | None) -> list[dict[str, Any]]:
    client = client or DeltaClient(s)
    try:
        contracts = products.all_contracts(client)
    except Exception as exc:
        return [{"event": "error", "where": "products", "error": str(exc)}]

    # measure the real top-of-book spread while we are here (Phase 3 cost path)
    for sym in s.symbols:
        try:
            charges.sample_spread(sym, market_data.depth(sym, client=client))
        except Exception:
            pass
        try:
            charges.sample_funding_rate(
                sym, market_data.ticker(sym, client=client).get("funding_rate")
            )
        except Exception:
            pass

    st = journal.load_state()
    fx = _fx_rate(client, s)
    now_utc = datetime.now(timezone.utc)
    in_ny = in_ny_window(s.ny_start, s.ny_end)
    ny_date = ny_session_date(s.ny_start, s.ny_end)
    # new entries fire only inside the lane window (default 17:00-05:30 IST);
    # the daytime belongs to the Indian lanes. Exits / hold-cap run regardless.
    entries_open = in_crypto_session(s.session_start, s.session_end, now_utc)
    events: list[dict[str, Any]] = []

    live = s.live_orders_enabled
    live_wallet = _live_wallet_usd(client) if live else 0.0
    if live:
        today = now_utc.date().isoformat()
        if st.get("_live_reconciled") != today:
            executor.reconcile(client)
            st["_live_reconciled"] = today
            journal.save_state(st)

    strategies = _enabled_strategies(s)
    _prune_removed_strategies(st, strategies, client, fx, now_utc, events)
    open_by_strat = _open_counts(st)  # after prune — pruned positions must not count

    for strat in strategies:
        for sym in _symbols_for(strat, s):
            contract = contracts.get(sym)
            if not contract or not contract.usable:
                events.append(
                    {
                        "event": "skip",
                        "strategy": strat,
                        "asset": sym,
                        "reason": "no usable contract",
                    }
                )
                continue
            key = f"{strat}:{sym}"
            slot = dict(st.get(key) or {})
            prev_strategy_state = slot.get("strategy")
            # outside the lane window with nothing open to manage → don't even
            # run the strategy: stepping it would churn its internal state
            # (armed levels, trade counters, traded-pivot lists) for an entry
            # we'd only suppress, and it wouldn't re-arm when the window opens.
            # A position opened earlier is still stepped so its exits fire.
            if not entries_open and not slot.get("position"):
                events.append(
                    {
                        "strategy": strat,
                        "asset": sym,
                        "event": "wait",
                        "reason": f"outside crypto session {s.session_start}-{s.session_end} IST",
                    }
                )
                continue
            try:
                # only fetch a live mark when there's a position to protect — an
                # idle strategy/symbol has nothing for it to react to, and this
                # is an extra API call per (strategy, symbol) every scan.
                live_px = _live_mark(sym, client) if slot.get("position") else None
                if strat == "ny_n_break":
                    c5 = _closed(market_data.candles(sym, "5m", days=2, client=client))
                    c15 = _closed(market_data.candles(sym, "15m", days=4, client=client))
                    # all-round: always "in session" (setup traded 24/7, NY hours
                    # unchanged), and the trade cap resets per UTC day.
                    nb_session = True if s.nbreak_allround else in_ny
                    nb_date = crypto_day(now_utc) if s.nbreak_allround else ny_date
                    new_state, ev = nb.step(
                        sym,
                        c5,
                        c15,
                        state=slot.get("strategy"),
                        cfg=_nb_cfg(s),
                        in_session=nb_session,
                        session_date=nb_date,
                        live_price=live_px,
                    )
                    day, frame = nb_date, c5
                elif strat == "ichimoku":
                    days = _ICHI_DAYS.get(s.ichimoku_tf, 15)
                    ch = _closed(market_data.candles(sym, s.ichimoku_tf, days=days, client=client))
                    new_state, ev = ichi.step(
                        sym, ch, state=slot.get("strategy"), cfg=_ichi_cfg(s), live_price=live_px
                    )
                    day, frame = crypto_day(now_utc), ch
                elif strat == "cpr_trend":
                    c5 = _closed(market_data.candles(sym, "5m", days=2, client=client))
                    c15 = _closed(market_data.candles(sym, "15m", days=4, client=client))
                    new_state, ev = cpr_trend.step(
                        sym,
                        c5,
                        c15,
                        state=slot.get("strategy"),
                        cfg=_cpr_trend_cfg(s),
                        live_price=live_px,
                    )
                    day, frame = crypto_day(now_utc), c5
                else:
                    mod, tf, days_n, cfg = _SIMPLE[strat](s)
                    fr = _closed(market_data.candles(sym, tf, days=days_n, client=client))
                    # ak_roxx_pro's own exit is its channel band, not the shared
                    # P&L trail (see its cfg.trail docstring) — its step()
                    # doesn't take live_price.
                    kwargs = {} if strat == "ak_roxx_pro" else {"live_price": live_px}
                    new_state, ev = mod.step(sym, fr, state=slot.get("strategy"), cfg=cfg, **kwargs)
                    day, frame = crypto_day(now_utc), fr

                slot["strategy"] = new_state
                action = ev.get("event")
                entered_signal = action == "enter"

                # crypto has no session — force-close a position held across more
                # than max_hold_days UTC-day boundaries, whatever the strategy
                # says. `action != "exit"` (not `not in (enter, exit)`) so a
                # strategy whose internal view desynced and keeps emitting
                # `enter` on a lane position we already hold still gets closed.
                if (
                    action != "exit"
                    and slot.get("position")
                    and _hold_exceeded(slot["position"], now_utc, s.max_hold_days)
                ):
                    action = "exit"
                    ev = {
                        "strategy": strat,
                        "asset": sym,
                        "event": "exit",
                        "side": slot["position"]["side"],
                        "price": float(frame["close"].iloc[-1]),
                        "reason": f"{s.max_hold_days}-day max hold",
                        "ts": str(frame["datetime"].iloc[-1]),
                    }
                    if isinstance(slot.get("strategy"), dict):
                        slot["strategy"]["position"] = None

                # a live position the strategy isn't closing this scan may have
                # been closed on the exchange (bracket stop / manual / liq)
                if (
                    live
                    and action != "exit"
                    and (slot.get("position") or {}).get("mode") == "live"
                    and _reap_exchange_close(client, slot, strat, sym, fx, ev)
                ):
                    slot["strategy"]["position"] = None
                    st[key] = slot
                    journal.save_state(st)
                    open_by_strat[strat] = max(0, open_by_strat.get(strat, 0) - 1)
                    events.append(
                        {
                            "strategy": strat,
                            "asset": sym,
                            "event": "reaped",
                            "reason": "closed on exchange",
                        }
                    )
                    continue

                # window closed while we still hold a position: manage it (exits
                # above already ran) but take no new entry the strategy emits.
                if action == "enter" and not entries_open:
                    events.append(
                        {
                            "strategy": strat,
                            "asset": sym,
                            "event": "wait",
                            "reason": "outside crypto session",
                        }
                    )
                    action = "wait"

                if action == "enter":
                    _apply_entry(
                        ev,
                        new_state,
                        slot,
                        s,
                        contract,
                        strat,
                        sym,
                        day,
                        now_utc,
                        open_by_strat.get(strat, 0),
                        sum(open_by_strat.values()),
                        frame,
                        client=client,
                        live=live,
                        live_wallet=live_wallet,
                    )
                    if slot.get("position"):
                        open_by_strat[strat] = open_by_strat.get(strat, 0) + 1
                elif action == "exit":
                    pos = slot.get("position") or {}
                    if pos.get("mode") == "live" and not _live_close(client, contract, pos, ev):
                        # live exit order failed — we are STILL exposed. Do not
                        # journal a close, do not clear state; retry next scan.
                        events.append(ev)
                        continue
                    row = _build_exit_row(ev, slot, strat, sym, fx)
                    if row is not None:
                        slot["position"] = None
                        st[key] = slot
                        journal.save_state(st)  # persist the close BEFORE journalling it
                        if not _already_journalled(row["exit_id"]):
                            journal.journal(row)
                            notify.crypto_closed(row)
                        open_by_strat[strat] = max(0, open_by_strat.get(strat, 0) - 1)
                        ev.update(pnl_usd=row["pnl_usd"], pnl_inr=row["pnl_inr"])
            except Exception as exc:
                events.append(
                    {"event": "error", "strategy": strat, "asset": sym, "error": str(exc)}
                )
                continue

            # a signal that fired "enter" but never became a real lane position
            # (rejected by max_concurrent/sizing/ML gate, or the window closed)
            # must not keep the strategy's per-tick state change either — it
            # already burned trades_today / consumed a swing level for nothing;
            # undo it so the same setup can be retried once capacity frees up.
            if entered_signal and not slot.get("position"):
                slot["strategy"] = prev_strategy_state

            st[key] = slot
            try:
                journal.save_state(st)  # persist each symbol's change as it happens
            except OSError:
                logger.warning("crypto_state.json write failed", exc_info=True)
            events.append(ev)

    try:
        journal.save_state(st)
    except OSError:
        pass
    return events


def _already_journalled(exit_id: str) -> bool:
    return any(r.get("exit_id") == exit_id for r in journal.recent(200))


def _entry_features(strat: str, sym: str, frame, side: str) -> dict[str, Any]:
    """Entry snapshot for the crypto model — defined in crypto.ml.features so
    capture and training share one definition."""
    from crypto.ml.features import entry_snapshot

    return entry_snapshot(strat, sym, frame, side)


def _live_wallet_usd(client: DeltaClient) -> float:
    try:
        bal = client.wallet() or []
        return sum(float(w.get("balance") or 0) for w in bal if isinstance(w, dict))
    except Exception:
        logger.warning("crypto live wallet read failed", exc_info=True)
        return 0.0


def _live_close(client, contract, pos: dict, ev: dict) -> bool:
    """Close a live position with a reduce-only order. Returns True when the
    position is (or already was) flat on Delta and the lane may journal the
    close; False only when Delta still shows it OPEN and the order failed — then
    the caller keeps the position and retries next scan."""
    sym = pos.get("asset", "")
    try:
        resp = executor.place_exit(
            client,
            contract,
            pos["side"],
            int(pos["size"]),
            client_order_id=f"x-{pos.get('strategy')}-{sym}-{ev.get('ts')}",
        )
        fp = executor.fill_price(client, resp.get("order_id"))
        if fp:
            ev["price"] = fp  # journal the real exit, not the signal candle close
        ev["live_order_id"] = resp.get("order_id")
        return True
    except Exception as exc:
        # The order failed. Maybe the position is already flat (exchange bracket
        # stop, manual close, liquidation) — in that case it IS closed, just
        # journal it. Only a genuinely-still-open position is a stuck exposure.
        state = executor.position_state(client, sym)
        if state == "flat":
            logger.warning(
                "crypto live exit rejected but %s is FLAT on Delta — closing locally", sym
            )
            ev["exit_price_source"] = "estimate"
            return True
        logger.error("LIVE EXIT FAILED for %s %s (Delta: %s): %s", sym, pos.get("side"), state, exc)
        notify.crypto_alert(
            f"\U0001f534 <b>CRYPTO LIVE EXIT FAILED</b> — {sym} still open ({state})\n{exc}",
            key=f"c-exitfail:{sym}:{pos.get('strategy')}",
        )
        return False


def _reap_exchange_close(client, slot: dict, strat: str, sym: str, fx: float, ev: dict) -> bool:
    """A live position the strategy is NOT exiting this scan — has it been closed
    on the exchange (bracket stop / manual / liquidation)? If Delta shows flat,
    journal the close at the last mark and clear state. Returns True if reaped."""
    pos = slot.get("position") or {}
    if pos.get("mode") != "live":
        return False
    if executor.position_state(client, sym) != "flat":
        return False
    try:
        mark = float(market_data.ticker(sym, client=client).get("mark_price") or 0) or None
    except Exception:
        mark = None
    close_ev = {
        "price": mark or pos.get("stop_price") or pos.get("entry_price"),
        "reason": "closed on exchange",
        "ts": ev.get("ts"),
        "exit_price_source": "estimate" if not mark else "mark",
    }
    row = _build_exit_row(close_ev, slot, strat, sym, fx)
    if row is not None:
        row["exit_price_source"] = close_ev["exit_price_source"]
        slot["position"] = None
        if not _already_journalled(row["exit_id"]):
            journal.journal(row)
            notify.crypto_closed(row)
        logger.info(
            "crypto: %s %s closed on the exchange (bracket/manual), journalled at ~%s",
            sym,
            str(pos.get("side")).upper(),
            close_ev["price"],
        )
    return True


def _apply_entry(
    ev,
    new_state,
    slot,
    s,
    contract,
    strat,
    sym,
    day,
    now_utc,
    strat_open,
    total_open,
    frame=None,
    *,
    client=None,
    live=False,
    live_wallet=0.0,
):
    if slot.get("position"):  # defensive — the engine already guards, but never double-open
        ev.update(event="hold", reason="position already open")
        return
    if strat_open >= s.max_concurrent:
        new_state["position"] = None
        ev.update(event="wait", reason=f"{strat}: max {s.max_concurrent} open (per strategy)")
        return
    if s.max_open_total and total_open >= s.max_open_total:
        new_state["position"] = None
        ev.update(
            event="wait", reason=f"portfolio cap: {s.max_open_total} open across all strategies"
        )
        return
    entry_px = float(ev["price"])
    side = ev["side"]
    wallet = live_wallet if live else s.paper_bankroll_usd
    lots = lots_from_margin_budget(
        contract, entry_px, margin_usd=s.margin_per_position_usd, leverage=s.leverage
    )
    sr = size_position(
        contract,
        entry_px,
        lots=lots,
        deploy_usd=s.deploy_usd,
        leverage=s.leverage,
        wallet_usd=wallet,
    )
    if not sr.ok:
        new_state["position"] = None
        reason = f"sizing: {sr.reason}"
        ev.update(event="wait", reason=reason)
        if live:
            _log_blocked_live(strat, sym, side, reason)
        return

    snapshot = _entry_features(strat, sym, frame, side) if frame is not None else {}
    g = ml_gate.check({"features": snapshot, "asset": sym})
    if not g["allowed"]:
        new_state["position"] = None
        reason = f"ML gate: {g['reason']}"
        ev.update(event="wait", reason=reason)
        if live:
            _log_blocked_live(strat, sym, side, reason)
        return

    order_id = None
    entry_src = "signal"
    fill_size = sr.size
    if live:
        ok, why = executor.live_gate(s)
        if not ok:
            new_state["position"] = None
            ev.update(event="wait", reason=why)
            _log_blocked_live(strat, sym, side, why)
            return
        try:
            resp = executor.place_entry(
                client,
                contract,
                side,
                sr.size,
                leverage=sr.leverage,
                sl_price=bracket_stop_price(entry_px, side, _trail_cfg_for(strat, s)),
                client_order_id=f"{strat}-{sym}-{ev.get('ts')}",
            )
        except Exception as exc:
            new_state["position"] = None  # order failed → we are flat, record nothing
            logger.error("crypto live entry FAILED for %s %s: %s", sym, side.upper(), exc)
            ev.update(event="live_rejected", reason=str(exc))
            _log_blocked_live(strat, sym, side, f"order rejected: {exc}")
            notify.crypto_alert(
                f"\U0001f534 <b>CRYPTO LIVE ENTRY FAILED</b> — {sym} {side.upper()}\n{exc}",
                key=f"c-entryfail:{sym}:{strat}",
            )
            return
        # THE ORDER IS LIVE. The position MUST be recorded from here — the fill
        # lookup is a soft refinement, never a reason to drop the position.
        order_id = resp.get("order_id")
        try:
            fp, fq = executor.fill_report(client, order_id)
            if fp:
                entry_px, entry_src = fp, "fill"
            if fq and fq >= 1:
                fill_size = int(fq)
        except Exception:
            logger.warning("crypto fill lookup failed — using signal price", exc_info=True)

    pos = {
        "strategy": strat,
        "asset": sym,
        "side": side,
        "day": day,
        "mode": "live" if live else "paper",
        "entry_price": entry_px,
        "entry_price_source": entry_src,
        "entry_time": ev.get("ts"),
        "size": fill_size,
        "contract_value": contract.contract_value,
        "leverage": sr.leverage,
        "margin_total_usd": sr.margin_total_usd,
        "notional_usd": round(fill_size * contract.contract_value * entry_px, 2),
        "stop_price": bracket_stop_price(entry_px, side, _trail_cfg_for(strat, s)),
        "opened_at": now_utc.isoformat(),
        "order_id": order_id,
        "entry_reason": ev.get("reason"),
        "features": snapshot,
    }
    slot["position"] = pos
    ev.update(
        size=fill_size,
        margin_usd=pos["margin_total_usd"],
        notional_usd=pos["notional_usd"],
        mode=pos["mode"],
    )
    notify.crypto_opened(pos)


def _known_strategies() -> set[str]:
    """Every strategy the current code can run — anything else in the state file
    is a leftover from a removed strategy (e.g. candle_renko)."""
    return {"ny_n_break", "ichimoku", "cpr_trend"} | set(_SIMPLE)


def _prune_removed_strategies(st, enabled, client, fx, now_utc, events) -> None:
    """Drop state slots for strategies that no longer exist in the code. If such
    a slot still holds a paper position, journal it closed at the current mark
    (reason: 'strategy removed') so it doesn't linger in the dashboard forever."""
    known = _known_strategies()
    changed = False
    for key in [k for k in list(st) if ":" in k and k.split(":", 1)[0] not in enabled]:
        strat, sym = key.split(":", 1)
        if strat in known:
            continue  # merely disabled, not removed — leave it
        slot = st.get(key) or {}
        pos = slot.get("position")
        if pos:
            try:
                mark = float(market_data.ticker(sym, client=client).get("mark_price") or 0)
            except Exception:
                mark = 0.0
            mark = mark or float(pos.get("entry_price") or 0)
            row = _build_exit_row(
                {"price": mark, "reason": "strategy removed", "ts": now_utc.isoformat()},
                slot,
                strat,
                sym,
                fx,
            )
            if row and not _already_journalled(row["exit_id"]):
                journal.journal(row)
                notify.crypto_closed(row)
                events.append(
                    {"strategy": strat, "asset": sym, "event": "exit", "reason": "strategy removed"}
                )
        st.pop(key, None)
        changed = True
    if changed:
        journal.save_state(st)


def _build_exit_row(ev, slot, strat, sym, fx) -> dict[str, Any] | None:
    """The closed-trade row. Does NOT persist — the caller saves state (position
    cleared) before appending this, so a crash between the two loses a record
    rather than double-counting P&L."""
    pos = slot.get("position")
    if not pos:
        return None
    exit_px = float(ev["price"])
    direction = 1 if pos["side"] == "long" else -1
    coins = float(pos["size"]) * float(pos["contract_value"])
    gross = (exit_px - float(pos["entry_price"])) * direction * coins
    cost = round_trip_cost_usd(
        float(pos["notional_usd"]), sym, exit_px, float(pos["size"]), float(pos["contract_value"])
    )
    exit_time = ev.get("ts") or datetime.now(timezone.utc).isoformat()
    funding = charges.funding_cost_usd(
        symbol=sym,
        side=pos["side"],
        notional_usd=float(pos["notional_usd"]),
        entry_time=pos.get("entry_time"),
        exit_time=exit_time,
    )
    pnl_usd = gross - cost - funding
    return {
        "venue": "delta",
        "day": pos["day"],
        "strategy": strat,
        "asset": sym,
        "mode": pos.get("mode", "paper"),
        "exit_id": f"{strat}:{sym}:{pos.get('entry_time')}:{pos.get('opened_at')}",
        "opened_at": pos.get("opened_at"),
        "order_id": pos.get("order_id"),
        "exit_order_id": ev.get("live_order_id"),
        "side": pos["side"],
        "size": pos["size"],
        "leverage": pos["leverage"],
        "entry_price": pos["entry_price"],
        "entry_time": pos["entry_time"],
        "exit_price": exit_px,
        "exit_time": exit_time,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "margin_usd": pos["margin_total_usd"],
        "notional_usd": pos["notional_usd"],
        "gross_usd": round(gross, 4),
        "fees_usd": round(cost, 4),
        "funding_usd": round(funding, 4),
        "pnl_usd": round(pnl_usd, 4),
        "pnl_inr": round(pnl_usd * fx, 2),
        "fx_usdinr": round(fx, 4),
        "pnl_pct": round(gross / float(pos["notional_usd"]) * 100.0, 4)
        if pos.get("notional_usd")
        else 0.0,
        "entry_reason": pos.get("entry_reason"),
        "exit_reason": ev.get("reason"),
        # from the strategy's own internal position (trailing.update_and_check
        # writes these there, not onto the lane's bookkeeping ``pos`` above) —
        # the strategy hands them over on the exit event since its own copy of
        # ``position`` is already cleared to None by the time we get here.
        "peak_pnl_pct": ev.get("peak_pnl_pct"),
        "trail_stop_pnl_pct": ev.get("trail_stop_pnl_pct"),
        "features": pos.get("features") or {},
    }


def close_position_manual(key: str, client: DeltaClient | None = None) -> dict[str, Any]:
    """The dashboard's manual "Close" button — force-exit one open position
    right now, at the current mark, instead of waiting for the strategy's own
    exit or the 1-day hold cap. Richard, 2026-09-11: "there should be a manual
    exit button for each open trade."

    Reuses the exact same close machinery the automatic paths use
    (``_live_close`` for a real order, ``_build_exit_row`` for the P&L math
    and journal row) — this just builds the same ``exit`` event by hand
    instead of getting it from ``step()`` or the hold-cap check, so a manual
    close can never compute P&L differently than an automatic one.

    Runs under ``_STATE_LOCK`` — the same lock ``_scan()`` holds for its whole
    cycle — so this can never interleave with the automatic scan loop reading
    or writing ``crypto_state.json``/the journal for the same (or any) key.
    """
    with _STATE_LOCK:
        return _close_position_manual_locked(key, client)


def _close_position_manual_locked(key: str, client: DeltaClient | None) -> dict[str, Any]:
    strat, _, sym = key.partition(":")
    if not strat or not sym:
        return {"ok": False, "error": f"bad position key {key!r}"}

    s = crypto_settings()
    client = client or DeltaClient(s)
    st = journal.load_state()
    slot = dict(st.get(key) or {})
    pos = slot.get("position")
    if not pos:
        return {"ok": False, "error": f"no open position for {key}"}

    try:
        mark = float(market_data.ticker(sym, client=client).get("mark_price") or 0)
    except Exception as exc:
        return {"ok": False, "error": f"couldn't fetch a live price for {sym}: {exc}"}
    if mark <= 0:
        return {"ok": False, "error": f"no live price for {sym} — try again"}

    ev: dict[str, Any] = {
        "strategy": strat,
        "asset": sym,
        "event": "exit",
        "side": pos["side"],
        "price": mark,
        "reason": "manual close",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if pos.get("mode") == "live":
        try:
            contract = products.all_contracts(client)[sym]
        except Exception as exc:
            return {"ok": False, "error": f"contract lookup failed for {sym}: {exc}"}
        if not _live_close(client, contract, pos, ev):
            return {
                "ok": False,
                "error": "the live close order failed — position is still open, see server log",
            }

    row = _build_exit_row(ev, slot, strat, sym, _fx_rate(client, s))
    if row is None:
        return {"ok": False, "error": "internal error building the exit row"}

    slot["position"] = None
    if isinstance(slot.get("strategy"), dict):
        slot["strategy"]["position"] = None
    st[key] = slot
    journal.save_state(st)  # persist the close before journalling it
    if not _already_journalled(row["exit_id"]):
        journal.journal(row)
        notify.crypto_closed(row)
    return {"ok": True, "trade": row}


def close_all_positions_manual(client: DeltaClient | None = None) -> dict[str, Any]:
    """The dashboard's "Close all" button — force-exit every currently open
    position (paper and live), one at a time, each through the exact same
    close_position_manual() path (and its lock) as an individual Close click.
    A key that fails (no live price, a failed live order) doesn't stop the
    rest — the caller sees which closed and which didn't."""
    keys = [k for k, v in journal.load_state().items() if isinstance(v, dict) and v.get("position")]
    client = client or DeltaClient(crypto_settings())
    results = {k: close_position_manual(k, client) for k in keys}
    failed = {k: r.get("error") for k, r in results.items() if not r.get("ok")}
    return {
        "ok": not failed,
        "attempted": len(keys),
        "closed": [k for k, r in results.items() if r.get("ok")],
        "failed": failed,
    }


if __name__ == "__main__":  # self-check — a fully-disabled lane is a no-op
    from dataclasses import replace

    off = replace(
        crypto_settings(),
        ny_nbreak_enabled=False,
        ichimoku_enabled=False,
        ak_roxx_enabled=False,
        bb_reversal_enabled=False,
        ema_jaguar_enabled=False,
        vp_edge_enabled=False,
        cpr_trend_enabled=False,
        rsi_adx_trend_enabled=False,
        trading_mode="PAPER",
        live_armed=False,
    )
    crypto_settings = lambda: off  # noqa: E731 — stub for the self-check
    assert scan_crypto_paper() == []
    assert not enabled()

    assert close_position_manual("not-a-real-key-no-colon")["ok"] is False
    assert (
        close_position_manual("ny_n_break:NOSUCHPOS")["ok"] is False
    )  # nothing open, no network hit

    # plain-language reasons for the trade-log's blocked-entry rows
    assert "Not enough money" in _plain_reason(
        "sizing: 3 lot(s) need $150.00, safe bankroll is $0.00"
    )
    assert "AI confidence check" in _plain_reason("ML gate: score 0.32 below 0.60")
    assert "internet address isn't approved" in _plain_reason(
        "order rejected: HTTP 401 — {'code': 'ip_not_whitelisted_for_api_key'}"
    )
    assert _plain_reason("some unrecognised reason") == "some unrecognised reason"

    # a blocked live entry is journaled separately from real trades
    import tempfile
    from pathlib import Path

    from crypto import journal as _j

    tmp = Path(tempfile.mkdtemp())
    _j.JOURNAL_PATH = tmp / "j.jsonl"
    _j.BLOCKED_PATH = tmp / "b.jsonl"
    _log_blocked_live("cpr_trend", "BTCUSD", "long", "sizing: no funds")
    assert len(_j.recent_blocked()) == 1
    assert _j.recent() == []  # never touches the real trades journal

    print("crypto.lanes self-check ok (all lanes off -> no-op)")
