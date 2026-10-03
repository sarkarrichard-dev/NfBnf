"""BTC daily options straddle — sell the at-the-money call+put on Delta each
evening, hold to next-day expiry.

Studied from a Theta Gainers YouTube video (2026-09-29): stack the ATM call +
put (a naked short straddle — no hedge leg, unlike this project's usual
2-leg-hedged rule; testing it anyway per Richard, 2026-09-29), take profit at
``btc_straddle_tp_fraction`` of the total credit collected, stop out at
``btc_straddle_sl_fraction`` of the total credit lost, otherwise let it expire
worthless or ITM.

A different position shape from every other crypto lane (two option legs, not
one perp side), so it runs its own tiny scan loop here rather than going
through ``crypto/lanes.py``'s single-leg-perp machinery. It shares the SAME
``crypto_state.json`` / ``crypto_journal.jsonl`` as everything else (own key,
``btc_daily_straddle:BTCUSD``), so it shows up in the existing per-(strategy,
instrument) Strategy P&L scorecard for free.

**Paper only, no live path at all.** ``crypto/delta/products.py`` only caches
perps, and ``crypto/executor.py`` only knows how to place a single-leg perp
order — there is nothing here that *could* go live yet even if the trading
mode were LIVE. This needs real paper trades before "should this go live" is
even a question worth asking.

ponytail: entry/exit fills are read from ``mark_price`` (no bid/ask crossing
simulated). Premiums are quoted in USD per 1 BTC of underlying, so money =
premium x ``size`` x ``contract_value`` (0.001 BTC per contract) -- see
``_btc_qty``. (Until 2026-10-03 this module multiplied by ``size`` alone, which
overstated every straddle P&L figure 1000x; the entry prices stored on a
position are the source of truth, so ``_credit`` re-derives the credit from
them instead of trusting a stored ``total_credit``.) The exchange fee is approximated as the same taker rate
applied to each leg's underlying notional (``size * contract_value *
entry_spot``, held fixed at the entry spot rather than re-priced per fill) —
Delta's real options fee schedule may cap it as a fraction of premium
instead; check a real fill before trusting the fee line specifically.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from crypto import journal
from crypto.charges import fee_usd
from crypto.config import crypto_settings
from crypto.delta import options as delta_options
from crypto.delta.client import DeltaClient
from crypto.delta.market_data import ticker
from crypto.session import in_ny_window
from index_ai import notify

logger = logging.getLogger(__name__)

STRATEGY = "btc_daily_straddle"
ASSET = "BTCUSD"
_KEY = f"{STRATEGY}:{ASSET}"


def enabled() -> bool:
    return crypto_settings().btc_straddle_enabled


def _fx_rate() -> float:
    try:
        return float(os.getenv("CRYPTO_USDINR", "88") or 88)
    except ValueError:
        return 88.0


def _leg_price(symbol: str, client: DeltaClient) -> float | None:
    try:
        px = float(ticker(symbol, client=client).get("mark_price") or 0)
        return px if px > 0 else None
    except Exception:
        return None


def is_straddle_position(pos: dict[str, Any]) -> bool:
    """The dashboard's generic /api/crypto/positions loop needs to tell this
    two-leg shape apart from every other (single-leg-perp) position it knows
    how to price — a straddle has no ``side``/``entry_price``/``notional_usd``,
    so running the perp P&L math on it would compute nonsense, not just be
    imprecise."""
    return "call_symbol" in pos


def _btc_qty(pos: dict[str, Any]) -> float:
    """BTC of underlying per leg: contracts x BTC per contract."""
    return float(pos["size"]) * float(pos["contract_value"])


def _credit(pos: dict[str, Any]) -> float:
    """Premium received, in USD, from the stored entry prices (per-BTC quotes)."""
    return (float(pos["call_entry"]) + float(pos["put_entry"])) * _btc_qty(pos)


def unrealized(pos: dict[str, Any], client: DeltaClient) -> dict[str, Any]:
    """Live mark-to-market for one open straddle, in the same shape the
    dashboard's positions endpoint expects (mark/unrealized_usd/...). None
    for a field it can't compute right now (a dead quote), never a fake 0."""
    call_mark = _leg_price(pos["call_symbol"], client)
    put_mark = _leg_price(pos["put_symbol"], client)
    if call_mark is None or put_mark is None:
        return {
            "mark": None,
            "unrealized_usd": None,
            "unrealized_inr": None,
            "unrealized_pct": None,
        }
    size = pos["size"]
    debit_now = (call_mark + put_mark) * _btc_qty(pos)
    gross = _credit(pos) - debit_now
    leg_notional = size * pos["contract_value"] * pos["entry_spot"]
    cost = fee_usd(leg_notional) * 4  # same approximation as _build_exit_row
    upnl = gross - cost
    credit = _credit(pos)
    return {
        "mark": round(call_mark + put_mark, 2),
        "unrealized_usd": round(upnl, 2),
        "unrealized_inr": round(upnl * _fx_rate(), 0),
        "unrealized_pct": round(upnl / credit * 100.0, 2) if credit else None,
        "unrealized_gross_usd": round(gross, 2),
        "accrued_cost_usd": round(cost, 2),
    }


def scan_btc_straddle_paper(client: DeltaClient | None = None) -> list[dict[str, Any]]:
    """Called on the same ~60s cadence as ``crypto.lanes.scan_crypto_paper``.
    Never raises — a broken quote this tick just means "try again next scan"."""
    s = crypto_settings()
    if not s.btc_straddle_enabled:
        return []
    own = client is None
    client = client or DeltaClient(s)
    try:
        # crypto_state.json is a shared file: crypto.lanes.scan_crypto_paper and
        # the dashboard's manual-close endpoint both hold this exact lock for
        # their whole load-mutate-save cycle (crypto/lanes.py's own docstring:
        # "can never interleave ... for the same (or any) key"). This module
        # must join that lock too, or a manual close on a different key can
        # read a stale snapshot and silently revert this module's write.
        from crypto.lanes import _STATE_LOCK

        with _STATE_LOCK:
            return _scan(s, client, now=datetime.now(timezone.utc))
    except Exception as exc:
        logger.warning("btc_straddle scan aborted", exc_info=True)
        return [{"event": "error", "strategy": STRATEGY, "asset": ASSET, "error": str(exc)}]
    finally:
        if own:
            client.close()


def _already_journalled(exit_id: str) -> bool:
    return any(r.get("exit_id") == exit_id for r in journal.recent(200))


def _scan(s, client: DeltaClient, *, now: datetime) -> list[dict[str, Any]]:
    st = journal.load_state()
    slot = dict(st.get(_KEY) or {})
    pos = slot.get("position")

    if pos:
        close = _manage_position(pos, client, now)  # mutates pos's last-seen marks in place
        if close is None:
            slot["position"] = pos
            st[_KEY] = slot
            journal.save_state(st)  # persist the last-seen marks even while just holding
            return [{"strategy": STRATEGY, "asset": ASSET, "event": "hold"}]
        row = _build_exit_row(pos, close)
        slot["position"] = None
        st[_KEY] = slot
        journal.save_state(st)  # persist the close before journalling it
        if not _already_journalled(row["exit_id"]):
            journal.journal(row)
            notify.crypto_straddle_closed(row)
        return [
            {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "exit",
                "reason": close["reason"],
                "pnl_usd": row["pnl_usd"],
            }
        ]

    if not in_ny_window(s.btc_straddle_entry_start, s.btc_straddle_entry_end, now):
        return [
            {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "wait",
                "reason": f"outside entry window {s.btc_straddle_entry_start}"
                f"-{s.btc_straddle_entry_end} IST",
            }
        ]

    today = now.date().isoformat()
    if slot.get("last_entry_day") == today:
        return [
            {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "wait",
                "reason": "already entered today",
            }
        ]

    entry = _try_entry(s, client, now)
    new_pos = entry.get("position")
    if new_pos is not None:
        slot["position"] = new_pos
        slot["last_entry_day"] = today
        st[_KEY] = slot
        journal.save_state(st)
        notify.crypto_straddle_opened(new_pos)
    return [entry["event"]]


def _manage_position(
    pos: dict[str, Any], client: DeltaClient, now: datetime
) -> dict[str, Any] | None:
    settlement = datetime.fromisoformat(pos["settlement"])
    call_mark = _leg_price(pos["call_symbol"], client)
    put_mark = _leg_price(pos["put_symbol"], client)
    if call_mark is not None:
        pos["last_call_mark"] = call_mark
    if put_mark is not None:
        pos["last_put_mark"] = put_mark
    if now >= settlement:
        # A settled contract's ticker often stops quoting within the same ~60s
        # poll that notices settlement has passed — falling back to 0.0 there
        # would record a deep-ITM loss as a full-credit win. Fall back to the
        # last quote we actually saw instead (still an approximation of the
        # true settlement value, but not a fabricated "worthless" price).
        return {
            "reason": "expiry",
            "call_exit": call_mark if call_mark is not None else pos.get("last_call_mark", 0.0),
            "put_exit": put_mark if put_mark is not None else pos.get("last_put_mark", 0.0),
            "exit_time": now.isoformat(),
        }
    if call_mark is None or put_mark is None:
        return None  # a quote failed this tick — try again next scan, don't force a bad exit
    # per-BTC premium units (same units as the marks), from the stored entry prices
    credit_per_contract = float(pos["call_entry"]) + float(pos["put_entry"])
    pnl_per_contract = credit_per_contract - (call_mark + put_mark)
    tp_level = credit_per_contract * pos["tp_fraction"]
    sl_level = -credit_per_contract * pos["sl_fraction"]
    if pnl_per_contract >= tp_level:
        return {
            "reason": "take_profit",
            "call_exit": call_mark,
            "put_exit": put_mark,
            "exit_time": now.isoformat(),
        }
    if pnl_per_contract <= sl_level:
        return {
            "reason": "stop_loss",
            "call_exit": call_mark,
            "put_exit": put_mark,
            "exit_time": now.isoformat(),
        }
    return None


def _try_entry(s, client: DeltaClient, now: datetime) -> dict[str, Any]:
    spot = _leg_price(ASSET, client)
    if not spot:
        return {
            "event": {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "wait",
                "reason": "no BTC spot price",
            }
        }
    pair = delta_options.nearest_expiry_atm_straddle(spot, client, now=now)
    if pair is None:
        return {
            "event": {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "wait",
                "reason": "no live BTC daily options listed",
            }
        }
    call, put = pair
    call_px = _leg_price(call.symbol, client)
    put_px = _leg_price(put.symbol, client)
    if not call_px or not put_px:
        return {
            "event": {
                "strategy": STRATEGY,
                "asset": ASSET,
                "event": "wait",
                "reason": "no live quote for the ATM legs",
            }
        }
    size = max(1, round(s.btc_straddle_size_btc / max(call.contract_value, 1e-9)))
    total_credit = (call_px + put_px) * size * call.contract_value
    today = now.date().isoformat()
    position = {
        "call_symbol": call.symbol,
        "put_symbol": put.symbol,
        "strike": call.strike,
        "call_entry": call_px,
        "put_entry": put_px,
        "size": size,
        "total_credit": total_credit,
        "tp_fraction": s.btc_straddle_tp_fraction,
        "sl_fraction": s.btc_straddle_sl_fraction,
        "entry_time": now.isoformat(),
        "opened_at": now.isoformat(),
        "settlement": call.settlement.isoformat(),
        "day": today,
        "entry_spot": spot,
        "contract_value": call.contract_value,
    }
    return {
        "position": position,
        "event": {
            "strategy": STRATEGY,
            "asset": ASSET,
            "event": "enter",
            "side": "short_straddle",
            "strike": call.strike,
            "reason": f"ATM straddle sold: {call.symbol} + {put.symbol}, "
            f"credit ${total_credit:.2f} for {size} contract(s)",
        },
    }


def _build_exit_row(pos: dict[str, Any], close: dict[str, Any]) -> dict[str, Any]:
    size = pos["size"]
    call_exit, put_exit = close["call_exit"], close["put_exit"]
    exit_debit = (call_exit + put_exit) * _btc_qty(pos)
    credit = _credit(pos)
    gross = credit - exit_debit
    # Fee basis is the underlying notional per leg (matches every other Delta
    # fee calc in this codebase — crypto/lanes.py, crypto/backtest.py — which
    # all charge the taker rate on size*contract_value*price, never on
    # premium). Two legs open + two legs close = 4x one leg's notional.
    leg_notional = size * pos["contract_value"] * pos["entry_spot"]
    fees = fee_usd(leg_notional) * 4
    pnl = gross - fees
    fx = _fx_rate()
    return {
        "venue": "delta",
        "day": pos["day"],
        "strategy": STRATEGY,
        "asset": ASSET,
        "mode": "paper",
        "call_symbol": pos["call_symbol"],
        "put_symbol": pos["put_symbol"],
        "strike": pos["strike"],
        "size": size,
        "entry_spot": pos.get("entry_spot"),
        "call_entry": pos["call_entry"],
        "put_entry": pos["put_entry"],
        "call_exit": call_exit,
        "put_exit": put_exit,
        "opened_at": pos["opened_at"],
        "entry_time": pos["entry_time"],
        "exit_time": close["exit_time"],
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "total_credit_usd": round(credit, 4),
        "exit_debit_usd": round(exit_debit, 4),
        "gross_usd": round(gross, 4),
        "fees_usd": round(fees, 4),
        "pnl_usd": round(pnl, 4),
        "pnl_inr": round(pnl * fx, 2),
        "fx_usdinr": round(fx, 4),
        "exit_reason": close["reason"],
        "exit_id": f"{STRATEGY}:{ASSET}:{pos['entry_time']}:{pos['opened_at']}",
    }


if __name__ == "__main__":  # self-check — no network
    import tempfile
    from dataclasses import replace
    from pathlib import Path

    from crypto.delta.options import OptionLeg

    tmp = Path(tempfile.mkdtemp())
    journal.STATE_PATH = tmp / "s.json"
    journal.JOURNAL_PATH = tmp / "j.jsonl"

    # This runs as a plain script, not under pytest, so conftest's autouse
    # Telegram-token-clearing fixture does NOT apply here — without this, a
    # real self-check run would fire real Telegram messages for fabricated
    # test trades through whatever bot token is sitting in .env (the exact
    # "test-suite leak" class of bug this project has been bitten by before).
    notify.send = lambda *a, **k: None

    _now = datetime(2026, 9, 29, 12, 30, tzinfo=timezone.utc)  # 18:00 IST
    settlement = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    call = OptionLeg("C-BTC-90000-300925", 2, 90000.0, True, settlement, 0.5, 0.001)
    put = OptionLeg("P-BTC-90000-300925", 3, 90000.0, False, settlement, 0.5, 0.001)

    prices = {"BTCUSD": 89700.0, call.symbol: 500.0, put.symbol: 480.0}
    globals()["_leg_price"] = lambda sym, _c: prices.get(sym)
    globals()["delta_options"] = type(
        "_M",
        (),
        {"nearest_expiry_atm_straddle": staticmethod(lambda spot, client, now=None: (call, put))},
    )

    off_hours = crypto_settings()
    off_hours = replace(off_hours, btc_straddle_enabled=True, btc_straddle_size_btc=0.001)
    globals()["crypto_settings"] = lambda: off_hours

    events = _scan(off_hours, None, now=_now)
    assert events[0]["event"] == "enter", events
    assert journal.load_state()[_KEY]["position"]["size"] == 1  # 0.001 BTC / 0.001 contract_value

    # price runs against us hard enough to hit the stop (credit 980, debit >= 1960)
    prices[call.symbol] = 1200.0
    prices[put.symbol] = 900.0
    from datetime import timedelta

    later = _now + timedelta(minutes=10)
    events = _scan(off_hours, None, now=later)
    assert events[0]["event"] == "exit" and events[0]["reason"] == "stop_loss", events
    rows = journal.recent(10)
    assert len(rows) == 1 and rows[0]["strategy"] == STRATEGY and rows[0]["pnl_usd"] < 0

    # re-entering the same day is refused even though the window is open
    events = _scan(off_hours, None, now=later)
    assert events[0]["reason"] == "already entered today", events

    # expiry with a dead quote falls back to the last SEEN mark, not 0.0 (a
    # deep-ITM loss must not be recorded as a full-credit win just because the
    # ticker went quiet right after settlement)
    journal.save_state({})  # reset — start a fresh position for this case
    now2 = _now + timedelta(minutes=30)  # distinct entry time so exit_id can't collide with above
    prices[call.symbol], prices[put.symbol] = 500.0, 480.0
    events = _scan(off_hours, None, now=now2)
    assert events[0]["event"] == "enter", events
    prices[call.symbol], prices[put.symbol] = (
        900.0,
        50.0,
    )  # last seen before the feed dies (deep ITM call)
    hold_tick = now2 + timedelta(minutes=5)
    events = _scan(off_hours, None, now=hold_tick)
    assert events[0]["event"] == "hold", events  # not a TP/SL level yet, just recording the mark
    prices[call.symbol] = None  # ticker goes dead exactly as settlement passes
    prices[put.symbol] = None
    past_settlement = settlement + timedelta(minutes=1)
    events = _scan(off_hours, None, now=past_settlement)
    assert events[0]["event"] == "exit" and events[0]["reason"] == "expiry", events
    row = journal.recent(10)[-1]
    assert row["call_exit"] == 900.0 and row["put_exit"] == 50.0, row  # last known, not 0.0
    # a 0.0 fallback would have booked the full ~$980 credit as pure profit;
    # pricing the real (deep-ITM) exit instead leaves only a small edge
    assert row["pnl_usd"] < 100, row

    print("crypto.btc_straddle self-check ok")
