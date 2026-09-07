"""Crypto paper lane — both strategies on BTC/ETH perps: size, journal, alert.

``scan_crypto_paper()`` is called from the scanner's crypto task (~60 s). It
never raises: one asset or strategy failing must not stop the others. Paper only
— there is no code path here that places an order.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto import charges, journal, notify
from crypto.charges import round_trip_cost_usd
from crypto.config import PERP_SYMBOLS, crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import DeltaClient
from crypto.session import crypto_day, in_ny_window, ny_session_date
from crypto.sizing import size_position
from crypto.strategies import ichimoku as ichi
from crypto.strategies import ny_n_break as nb

logger = logging.getLogger(__name__)

_ICHI_DAYS = {"15m": 4, "30m": 8, "1h": 15, "2h": 25, "4h": 45, "6h": 60, "1d": 260}


def enabled() -> bool:
    return crypto_settings().paper_enabled


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
    if not s.paper_enabled:
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
    for sym in PERP_SYMBOLS:
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

    strategies = []
    if s.ny_nbreak_enabled:
        strategies.append("ny_n_break")
    if s.ichimoku_enabled:
        strategies.append("ichimoku")

    for strat in strategies:
        for sym in PERP_SYMBOLS:
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

                if action == "enter":
                    _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc,
                                 open_slots, frame)
                    if slot.get("position"):
                        open_slots += 1
                elif action == "exit":
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
    """A small snapshot captured at entry for a future crypto model. Capture
    only — the modelling is a separate follow-up."""
    import pandas as pd

    feats: dict[str, Any] = {
        "venue": "delta",
        "is_btc": 1.0 if sym == "BTCUSD" else 0.0,
        "strategy_ny_n_break": 1.0 if strat == "ny_n_break" else 0.0,
        "side_long": 1.0 if side == "long" else 0.0,
    }
    try:
        c = frame["close"].astype(float)
        price = float(c.iloc[-1])
        hi, lo = frame["high"].astype(float), frame["low"].astype(float)
        tr = (hi - lo).rolling(14).mean().iloc[-1]
        feats["atr_pct"] = round(float(tr) / price * 100.0, 4) if price else 0.0
        feats["ret_20_pct"] = round((price / float(c.iloc[-21]) - 1) * 100.0, 4) if len(c) > 21 else 0.0
        feats["entry_hour_utc"] = int(pd.Timestamp(frame["datetime"].iloc[-1]).hour)
        if strat == "ny_n_break":
            from crypto.strategies.indicators import anchored_vwap, ema

            feats["dist_ema25_pct"] = round((price / float(ema(c, 25).iloc[-1]) - 1) * 100.0, 4)
            feats["dist_vwap_pct"] = round((price / float(anchored_vwap(frame).iloc[-1]) - 1) * 100.0, 4)
        else:
            from index_ai.strategies.ichimoku import compute_ichimoku

            row = compute_ichimoku(frame).iloc[-1]
            if not pd.isna(row["cloud_top"]):
                feats["dist_cloud_top_pct"] = round((price / float(row["cloud_top"]) - 1) * 100.0, 4)
                feats["dist_kijun_pct"] = round((price / float(row["kijun"]) - 1) * 100.0, 4)
    except Exception:
        pass
    return feats


def _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc, open_slots, frame=None):
    if slot.get("position"):  # defensive — the engine already guards, but never double-open
        ev.update(event="hold", reason="position already open")
        return
    if open_slots >= s.max_concurrent:
        new_state["position"] = None
        ev.update(event="wait", reason=f"max {s.max_concurrent} concurrent positions")
        return
    entry_px = float(ev["price"])
    side = ev["side"]
    sr = size_position(
        contract, entry_px, deploy_usd=s.deploy_usd, leverage=s.leverage,
        wallet_usd=s.paper_bankroll_usd, allow_min_one=s.allow_min_one,
    )
    if not sr.ok:
        new_state["position"] = None
        ev.update(event="wait", reason=f"sizing: {sr.reason}")
        return
    pos = {
        "strategy": strat, "asset": sym, "side": side, "day": day,
        "entry_price": entry_px, "entry_time": ev.get("ts"),
        "size": sr.size, "contract_value": contract.contract_value,
        "leverage": sr.leverage, "margin_total_usd": sr.margin_total_usd,
        "notional_usd": sr.notional_usd,
        "stop_price": _stop_price(side, entry_px, _sl_pct(strat, s)),
        "opened_at": now_utc.isoformat(),
        "features": _entry_features(strat, sym, frame, side) if frame is not None else {},
    }
    slot["position"] = pos
    ev.update(size=sr.size, margin_usd=sr.margin_total_usd, notional_usd=sr.notional_usd)
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
        "exit_id": f"{strat}:{sym}:{pos.get('entry_time')}:{pos.get('opened_at')}",
        "opened_at": pos.get("opened_at"),
        "side": pos["side"], "size": pos["size"], "leverage": pos["leverage"],
        "entry_price": pos["entry_price"], "entry_time": pos["entry_time"],
        "exit_price": exit_px, "exit_time": ev.get("ts"),
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


if __name__ == "__main__":  # self-check — disabled lane is a no-op
    import os as _os

    _os.environ["ENABLE_CRYPTO_PAPER"] = "false"
    assert scan_crypto_paper() == []
    assert not enabled()
    print("crypto.lanes self-check ok (paper disabled -> no-op)")
