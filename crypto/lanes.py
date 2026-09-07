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

from crypto import journal, notify
from crypto.charges import round_trip_cost_usd
from crypto.config import PERP_SYMBOLS, crypto_settings
from crypto.delta import market_data, products
from crypto.delta.client import DeltaClient, DeltaError
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
            bal = client.wallet()
            usd = sum(float(w.get("balance") or 0) for w in bal)
            inr = sum(float(w.get("balance_inr") or 0) for w in bal)
            if usd > 0 and inr > 0:
                return inr / usd
        except DeltaError:
            pass
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
    client = client or DeltaClient(s)

    try:
        contracts = products.all_contracts(client)
    except DeltaError as exc:
        return [{"event": "error", "where": "products", "error": str(exc)}]

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
                    day = ny_date
                else:
                    days = _ICHI_DAYS.get(s.ichimoku_tf, 15)
                    ch = _closed(market_data.candles(sym, s.ichimoku_tf, days=days, client=client))
                    new_state, ev = ichi.step(sym, ch, state=slot.get("strategy"), cfg=_ichi_cfg(s))
                    day = crypto_day(now_utc)
            except (DeltaError, ValueError, KeyError) as exc:
                events.append({"event": "error", "strategy": strat, "asset": sym, "error": str(exc)})
                continue

            slot["strategy"] = new_state
            action = ev.get("event")

            if action == "enter":
                _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc, open_slots)
                if slot.get("position"):
                    open_slots += 1
            elif action == "exit":
                _apply_exit(ev, slot, strat, sym, fx)
                open_slots = max(0, open_slots - 1)

            st[key] = slot
            events.append(ev)

    _maybe_day_summary(st, s, in_ny, ny_date)
    journal.save_state(st)
    return events


def _apply_entry(ev, new_state, slot, s, contract, strat, sym, day, now_utc, open_slots):
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
    }
    slot["position"] = pos
    ev.update(size=sr.size, margin_usd=sr.margin_total_usd, notional_usd=sr.notional_usd)
    notify.opened(pos)


def _apply_exit(ev, slot, strat, sym, fx):
    pos = slot.get("position")
    if not pos:
        return
    exit_px = float(ev["price"])
    direction = 1 if pos["side"] == "long" else -1
    coins = float(pos["size"]) * float(pos["contract_value"])
    gross = (exit_px - float(pos["entry_price"])) * direction * coins
    cost = round_trip_cost_usd(
        float(pos["notional_usd"]), sym, exit_px, float(pos["size"]), float(pos["contract_value"])
    )
    pnl_usd = gross - cost
    row = {
        "venue": "delta", "day": pos["day"], "strategy": strat, "asset": sym,
        "side": pos["side"], "size": pos["size"], "leverage": pos["leverage"],
        "entry_price": pos["entry_price"], "entry_time": pos["entry_time"],
        "exit_price": exit_px, "exit_time": ev.get("ts"),
        "margin_usd": pos["margin_total_usd"], "notional_usd": pos["notional_usd"],
        "gross_usd": round(gross, 4), "fees_usd": round(cost, 4),
        "pnl_usd": round(pnl_usd, 4), "pnl_inr": round(pnl_usd * fx, 2), "fx_usdinr": round(fx, 4),
        "exit_reason": ev.get("reason"),
        "features": {},  # populated in Phase 3
    }
    journal.journal(row)
    notify.closed(row)
    slot["position"] = None
    ev.update(pnl_usd=row["pnl_usd"], pnl_inr=row["pnl_inr"])


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
