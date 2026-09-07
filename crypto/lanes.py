"""Crypto lane — both strategies on BTC/ETH perps: size, journal, alert.

``scan_crypto_paper()`` is called from the scanner's crypto task (~60 s). It
never raises: one asset or strategy failing must not stop the others. Paper is
the default; a real Delta order is placed only when ``live_orders_enabled`` is
true (LIVE mode + armed + credentials) — see ``crypto.executor``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto import charges, executor, journal, notify
from crypto.charges import round_trip_cost_usd
from crypto.config import crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import DeltaClient
from crypto.ml import gate as ml_gate
from crypto.session import crypto_day, in_ny_window, ny_session_date
from crypto.sizing import size_position
from crypto.strategies import ichimoku as ichi
from crypto.strategies import ny_n_break as nb

logger = logging.getLogger(__name__)

_ICHI_DAYS = {"15m": 4, "30m": 8, "1h": 15, "2h": 25, "4h": 45, "6h": 60, "1d": 260}

_last_alert: dict[str, float] = {}


def _alert(text: str, *, key: str | None = None, min_gap_s: float = 600.0) -> None:
    """Fire-and-forget Telegram, rate-limited per key so a stuck-state loop
    can't send an alert every 60 s."""
    import time as _t

    if key is not None:
        now = _t.monotonic()
        if now - _last_alert.get(key, 0.0) < min_gap_s:
            return
        _last_alert[key] = now
    try:
        notify.send(text)
    except Exception:
        pass


def enabled() -> bool:
    """The lane runs when paper is on, OR when live orders are fully armed
    (LIVE mode is meaningless without the lane running)."""
    s = crypto_settings()
    return s.paper_enabled or s.live_orders_enabled


def _closed(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the still-forming last bar so signals fire on closed candles only."""
    return df.iloc[:-1].reset_index(drop=True) if len(df) > 1 else df


def _nb_cfg(s) -> nb.NBreakConfig:
    return nb.NBreakConfig(
        max_trades_per_session=int(os.getenv("CRYPTO_NBREAK_MAX_TRADES", "3") or 3),
        sl_pct=s.nbreak_sl_pct,
    )


def _ichi_cfg(s) -> ichi.IchimokuConfig:
    return ichi.IchimokuConfig(sl_pct=s.ichimoku_sl_pct)


def _sl_pct(strategy: str, s) -> float:
    return s.nbreak_sl_pct if strategy == "ny_n_break" else s.ichimoku_sl_pct


def _stop_price(side: str, entry: float, sl_pct: float) -> float | None:
    if sl_pct <= 0:
        return None
    return entry * (1 - sl_pct / 100) if side == "long" else entry * (1 + sl_pct / 100)


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


def _open_count(state: dict[str, Any]) -> int:
    return sum(1 for k, v in state.items() if ":" in k and isinstance(v, dict) and v.get("position"))


def scan_crypto_paper(client: DeltaClient | None = None) -> list[dict[str, Any]]:
    s = crypto_settings()
    if not (s.paper_enabled or s.live_orders_enabled):
        return []
    try:
        return _scan(s, client)
    except Exception as exc:  # the "never raises" contract — the loop must survive
        logger.warning("crypto scan aborted", exc_info=True)
        return [{"event": "error", "where": "scan", "error": str(exc)}]


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

    st = journal.load_state()
    fx = _fx_rate(client, s)
    open_slots = _open_count(st)
    now_utc = datetime.now(timezone.utc)
    in_ny = in_ny_window(s.ny_start, s.ny_end)
    ny_date = ny_session_date(s.ny_start, s.ny_end)
    events: list[dict[str, Any]] = []

    live = s.live_orders_enabled
    live_wallet = _live_wallet_usd(client) if live else 0.0
    if live:
        today = now_utc.date().isoformat()
        if st.get("_live_reconciled") != today:
            executor.reconcile(client)
            st["_live_reconciled"] = today
            journal.save_state(st)

    strategies = []
    if s.ny_nbreak_enabled:
        strategies.append("ny_n_break")
    if s.ichimoku_enabled:
        strategies.append("ichimoku")

    for strat in strategies:
        for sym in s.symbols:
            contract = contracts.get(sym)
            if not contract or not contract.usable:
                events.append({"event": "skip", "strategy": strat, "asset": sym,
                               "reason": "no usable contract"})
                continue
            key = f"{strat}:{sym}"
            slot = dict(st.get(key) or {})
            try:
                if strat == "ny_n_break":
                    c5 = _closed(market_data.candles(sym, "5m", days=2, client=client))
                    c15 = _closed(market_data.candles(sym, "15m", days=4, client=client))
                    new_state, ev = nb.step(
                        sym, c5, c15, state=slot.get("strategy"), cfg=_nb_cfg(s),
                        in_session=in_ny, session_date=ny_date,
                    )
                    day, frame = ny_date, c5
                else:
                    days = _ICHI_DAYS.get(s.ichimoku_tf, 15)
                    ch = _closed(market_data.candles(sym, s.ichimoku_tf, days=days, client=client))
                    new_state, ev = ichi.step(sym, ch, state=slot.get("strategy"), cfg=_ichi_cfg(s))
                    day, frame = crypto_day(now_utc), ch

                slot["strategy"] = new_state
                action = ev.get("event")

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
                    open_slots = max(0, open_slots - 1)
                    events.append({"strategy": strat, "asset": sym, "event": "reaped",
                                   "reason": "closed on exchange"})
                    continue

                if action == "enter":
                    _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc,
                                 open_slots, frame, client=client, live=live,
                                 live_wallet=live_wallet)
                    if slot.get("position"):
                        open_slots += 1
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
                            notify.closed(row)
                        open_slots = max(0, open_slots - 1)
                        ev.update(pnl_usd=row["pnl_usd"], pnl_inr=row["pnl_inr"])
            except Exception as exc:
                events.append({"event": "error", "strategy": strat, "asset": sym, "error": str(exc)})
                continue

            st[key] = slot
            try:
                journal.save_state(st)  # persist each symbol's change as it happens
            except OSError:
                logger.warning("crypto_state.json write failed", exc_info=True)
            events.append(ev)

    _maybe_day_summary(st, s, in_ny, ny_date)
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
            client, contract, pos["side"], int(pos["size"]),
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
            logger.warning("crypto live exit rejected but %s is FLAT on Delta — closing locally", sym)
            ev["exit_price_source"] = "estimate"
            return True
        logger.error("LIVE EXIT FAILED for %s %s (Delta: %s): %s", sym, pos.get("side"), state, exc)
        _alert(
            f"\U0001f534 <b>CRYPTO LIVE EXIT FAILED</b> — {sym} {str(pos.get('side')).upper()} "
            f"still open ({state})\n{exc}",
            key=f"exitfail:{sym}:{pos.get('strategy')}",
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
        "reason": "closed on exchange", "ts": ev.get("ts"),
        "exit_price_source": "estimate" if not mark else "mark",
    }
    row = _build_exit_row(close_ev, slot, strat, sym, fx)
    if row is not None:
        row["exit_price_source"] = close_ev["exit_price_source"]
        slot["position"] = None
        if not _already_journalled(row["exit_id"]):
            journal.journal(row)
            notify.closed(row)
        _alert(
            f"ℹ️ <b>CRYPTO</b> — {sym} {pos.get('side','').upper()} closed on the "
            f"exchange (bracket stop / manual). Journalled at ~{close_ev['price']}.",
            key=f"reap:{sym}:{strat}",
        )
    return True


def _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc, open_slots,
                 frame=None, *, client=None, live=False, live_wallet=0.0):
    if slot.get("position"):  # defensive — the engine already guards, but never double-open
        ev.update(event="hold", reason="position already open")
        return
    if open_slots >= s.max_concurrent:
        new_state["position"] = None
        ev.update(event="wait", reason=f"max {s.max_concurrent} concurrent positions")
        return
    entry_px = float(ev["price"])
    side = ev["side"]
    wallet = live_wallet if live else s.paper_bankroll_usd
    sr = size_position(
        contract, entry_px, lots=s.lots, deploy_usd=s.deploy_usd, leverage=s.leverage,
        wallet_usd=wallet, allow_min_one=s.allow_min_one,
    )
    if not sr.ok:
        new_state["position"] = None
        ev.update(event="wait", reason=f"sizing: {sr.reason}")
        return

    snapshot = _entry_features(strat, sym, frame, side) if frame is not None else {}
    g = ml_gate.check({"features": snapshot, "asset": sym})
    if not g["allowed"]:
        new_state["position"] = None
        ev.update(event="wait", reason=f"ML gate: {g['reason']}")
        return

    order_id = None
    entry_src = "signal"
    fill_size = sr.size
    if live:
        ok, why = executor.live_gate(s)
        if not ok:
            new_state["position"] = None
            ev.update(event="wait", reason=why)
            return
        try:
            resp = executor.place_entry(
                client, contract, side, sr.size, leverage=sr.leverage,
                sl_price=_stop_price(side, entry_px, _sl_pct(strat, s)),
                client_order_id=f"{strat}-{sym}-{ev.get('ts')}",
            )
        except Exception as exc:
            new_state["position"] = None  # order failed → we are flat, record nothing
            logger.error("crypto live entry failed: %s", exc)
            _alert(f"\U0001f534 <b>CRYPTO LIVE ENTRY FAILED</b> — {sym} {side.upper()}\n{exc}")
            ev.update(event="live_rejected", reason=str(exc))
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
        "strategy": strat, "asset": sym, "side": side, "day": day,
        "mode": "live" if live else "paper",
        "entry_price": entry_px, "entry_price_source": entry_src,
        "entry_time": ev.get("ts"),
        "size": fill_size, "contract_value": contract.contract_value,
        "leverage": sr.leverage, "margin_total_usd": sr.margin_total_usd,
        "notional_usd": round(fill_size * contract.contract_value * entry_px, 2),
        "stop_price": _stop_price(side, entry_px, _sl_pct(strat, s)),
        "opened_at": now_utc.isoformat(),
        "order_id": order_id,
        "features": snapshot,
    }
    slot["position"] = pos
    ev.update(size=fill_size, margin_usd=pos["margin_total_usd"], notional_usd=pos["notional_usd"],
              mode=pos["mode"])
    notify.opened(pos)


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
    pnl_usd = gross - cost
    return {
        "venue": "delta", "day": pos["day"], "strategy": strat, "asset": sym,
        "mode": pos.get("mode", "paper"),
        "exit_id": f"{strat}:{sym}:{pos.get('entry_time')}:{pos.get('opened_at')}",
        "opened_at": pos.get("opened_at"),
        "order_id": pos.get("order_id"), "exit_order_id": ev.get("live_order_id"),
        "side": pos["side"], "size": pos["size"], "leverage": pos["leverage"],
        "entry_price": pos["entry_price"], "entry_time": pos["entry_time"],
        "exit_price": exit_px, "exit_time": ev.get("ts"),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "margin_usd": pos["margin_total_usd"], "notional_usd": pos["notional_usd"],
        "gross_usd": round(gross, 4), "fees_usd": round(cost, 4),
        "pnl_usd": round(pnl_usd, 4), "pnl_inr": round(pnl_usd * fx, 2), "fx_usdinr": round(fx, 4),
        "pnl_pct": round(gross / float(pos["notional_usd"]) * 100.0, 4) if pos.get("notional_usd") else 0.0,
        "exit_reason": ev.get("reason"),
        "features": pos.get("features") or {},
    }


def _maybe_day_summary(st: dict[str, Any], s, in_ny: bool, ny_date: str) -> None:
    meta = dict(st.get("_ny") or {})
    if in_ny:
        meta["active"] = ny_date
        st["_ny"] = meta
        return
    active = meta.get("active")
    if s.ny_nbreak_enabled and active and meta.get("summarised") != active:
        rows = journal.day_rows(active, strategy="ny_n_break")
        if rows:
            notify.day_summary(active, rows)
        meta["summarised"] = active
        meta["active"] = None
        st["_ny"] = meta


if __name__ == "__main__":  # self-check — a fully-disabled lane is a no-op
    from dataclasses import replace

    off = replace(crypto_settings(), paper_enabled=False, trading_mode="PAPER", live_armed=False)
    crypto_settings = lambda: off  # noqa: E731 — stub for the self-check
    assert scan_crypto_paper() == []
    assert not off.live_orders_enabled
    print("crypto.lanes self-check ok (all lanes off -> no-op)")
