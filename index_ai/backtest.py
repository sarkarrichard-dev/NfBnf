"""
Replay intraday strategy signals on Dhan index candles (interval from CANDLE_INTERVAL_MINUTES).

Uses local candle cache (memory/candles/) for lookbacks beyond Dhan's ~5-day
intraday window. Option PnL can use a simplified Greeks proxy.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from index_ai.backtest_options import estimate_option_pnl_rupees
from index_ai.candle_cache import fetch_backtest_candles, sync_all_configured
from index_ai.candles import prepare_intraday_signal_frames
from index_ai.config import AppSettings, candle_interval_minutes
from index_ai.dhan import DhanClient
from index_ai.ichimoku import cloud_reentry_exit
from index_ai.instruments import IndexInstrument, configured_index_keys, get_instrument
from index_ai.market_clock import session_times
from index_ai.strategy import StrategySignal
from index_ai.strategy_params import get_strategy_params
from index_ai.strategy_router import route_intraday_signal, strategy_style

_BUY_ACTIONS = frozenset({"BUY_CALL", "BUY_PUT"})

_BULLISH = frozenset({"BUY_CALL", "SELL_BULL_PUT_SPREAD", "SELL_ATM_PUT"})
_BEARISH = frozenset({"BUY_PUT", "SELL_BEAR_CALL_SPREAD", "SELL_ATM_CALL"})
_CREDIT_RANGE = frozenset({"SELL_IRON_CONDOR"})
_ACTIONABLE = _BULLISH | _BEARISH | _CREDIT_RANGE


def _sessions(frame: pd.DataFrame) -> list[tuple[Any, pd.DataFrame]]:
    work = frame.copy()
    work["session"] = pd.to_datetime(work["datetime"]).dt.date
    out: list[tuple[Any, pd.DataFrame]] = []
    for day in sorted(work["session"].unique()):
        day_frame = work[work["session"] == day].reset_index(drop=True)
        if not day_frame.empty:
            out.append((day, day_frame))
    return out


def _bar_time_allowed(ts: pd.Timestamp, bounds: dict[str, Any]) -> bool:
    t = ts.to_pydatetime().time()
    return bounds["entries_start"] <= t <= bounds["entries_end"]


def _spot_proxy_points(action: str, entry: float, exit_px: float) -> float:
    move = float(exit_px) - float(entry)
    if action in _BULLISH:
        return move
    if action in _BEARISH:
        return -move
    if action in _CREDIT_RANGE:
        return -abs(move) * 0.35
    return 0.0


def _trade_pnl(
    action: str,
    entry: float,
    exit_px: float,
    *,
    instrument: IndexInstrument,
    entry_time: str,
    exit_time: str,
    pnl_mode: str,
) -> dict[str, float]:
    if pnl_mode == "option_proxy":
        try:
            hold = (
                pd.Timestamp(exit_time) - pd.Timestamp(entry_time)
            ).total_seconds() / 60.0
        except Exception:
            hold = 30.0
        est = estimate_option_pnl_rupees(
            action,
            entry,
            exit_px,
            lot_size=instrument.lot_size,
            hold_minutes=max(5.0, hold),
        )
        return {
            "proxy_index_points": est["proxy_index_points"],
            "proxy_pnl_rupees": est["proxy_pnl_rupees"],
            "gross_proxy_pnl_rupees": est["gross_proxy_pnl_rupees"],
            "estimated_friction_rupees": est["estimated_friction_rupees"],
        }
    points = _spot_proxy_points(action, entry, exit_px)
    return {
        "proxy_index_points": round(points, 2),
        "proxy_pnl_rupees": round(points * instrument.lot_size * 0.25, 2),
        "gross_proxy_pnl_rupees": round(points * instrument.lot_size * 0.25, 2),
        "estimated_friction_rupees": 0.0,
    }


def _should_exit(open_action: str, signal: StrategySignal) -> bool:
    if signal.action == "NO_TRADE":
        return True
    if signal.action == open_action:
        return False
    if signal.action in _ACTIONABLE:
        return True
    return False


def replay_session(
    today: pd.DataFrame,
    previous: pd.DataFrame,
    *,
    instrument: IndexInstrument,
    allow_option_selling: bool,
    bounds: dict[str, Any] | None = None,
    pnl_mode: str = "option_proxy",
    cloud_exit: bool = False,
) -> list[dict[str, Any]]:
    """Bar-by-bar signal replay for one session; returns closed proxy trades.

    When ``cloud_exit`` is set, an open BUY_CALL / BUY_PUT is also closed once
    spot closes back into the Ichimoku cloud (see :mod:`index_ai.ichimoku`).
    """
    bounds = bounds or session_times()
    sp = get_strategy_params()
    min_bars = sp.ema_slow_period + 2
    prev_tail = (
        previous.tail(sp.ichimoku_span_b_period + sp.ichimoku_base_period)
        if cloud_exit and not previous.empty
        else None
    )
    trades: list[dict[str, Any]] = []
    open_trade: dict[str, Any] | None = None
    pending_entry: dict[str, Any] | None = None
    pending_exit_action: str | None = None

    def _close_trade(exit_ts: str, exit_px: float, exit_action: str) -> None:
        nonlocal open_trade
        if open_trade is None:
            return
        pnl = _trade_pnl(
            str(open_trade["action"]),
            float(open_trade["entry_price"]),
            exit_px,
            instrument=instrument,
            entry_time=str(open_trade["entry_time"]),
            exit_time=exit_ts,
            pnl_mode=pnl_mode,
        )
        trades.append(
            {
                **open_trade,
                "exit_time": exit_ts,
                "exit_price": exit_px,
                "exit_action": exit_action,
                "pnl_mode": pnl_mode,
                **pnl,
                "aligned": pnl["proxy_pnl_rupees"] > 0,
            }
        )
        open_trade = None

    for i in range(min_bars, len(today)):
        row = today.iloc[i]
        ts = pd.Timestamp(row["datetime"])

        # Signals are only knowable at a candle close. Execute them at the
        # following candle's open; this removes same-bar look-ahead fills.
        if pending_exit_action and open_trade is not None:
            _close_trade(str(ts), float(row["open"]), pending_exit_action)
            pending_exit_action = None
        if pending_entry is not None and open_trade is None:
            if _bar_time_allowed(ts, bounds):
                open_trade = {
                    **pending_entry,
                    "entry_time": str(ts),
                    "entry_price": float(row["open"]),
                    "execution_model": "next_bar_open",
                }
            pending_entry = None

        slice_frame = today.iloc[: i + 1].reset_index(drop=True)
        try:
            signal, regime = route_intraday_signal(
                slice_frame,
                previous,
                allow_option_selling=allow_option_selling,
            )
        except Exception:
            continue

        if open_trade is None:
            if (
                signal.action in _ACTIONABLE
                and _bar_time_allowed(ts, bounds)
                and (
                    signal.confidence >= sp.credit_min_confidence
                    or signal.action in {"BUY_CALL", "BUY_PUT"}
                )
            ):
                pending_entry = {
                    "signal_time": str(ts),
                    "action": signal.action,
                    "strategy_mode": signal.strategy_mode,
                    "confidence": signal.confidence,
                    "cpr_regime": regime.day_bias,
                    "reason": signal.reason,
                }
            continue

        open_action = str(open_trade["action"])
        if cloud_exit and open_action in _BUY_ACTIONS and _bar_time_allowed(ts, bounds):
            edir = 1 if open_action == "BUY_CALL" else -1
            cloud_frame = (
                pd.concat([prev_tail, slice_frame], ignore_index=True)
                if prev_tail is not None
                else slice_frame
            )
            hit, _reason = cloud_reentry_exit(
                edir,
                cloud_frame,
                conversion=sp.ichimoku_conversion_period,
                base=sp.ichimoku_base_period,
                span_b=sp.ichimoku_span_b_period,
            )
            if hit:
                pending_exit_action = "CLOUD_EXIT"
                continue

        if _should_exit(open_action, signal) or not _bar_time_allowed(ts, bounds):
            pending_exit_action = signal.action if signal.action != "NO_TRADE" else "SIGNAL_EXIT"

    if open_trade is not None:
        last = today.iloc[-1]
        _close_trade(str(last["datetime"]), float(last["close"]), "SESSION_END")

    return trades


def _replay_candles(
    candles: pd.DataFrame,
    *,
    instrument: IndexInstrument,
    app_settings: AppSettings,
    pnl_mode: str,
    cloud_exit: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[Any, pd.DataFrame]]]:
    sp = get_strategy_params()
    bounds = session_times()
    sessions = _sessions(candles)
    all_trades: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []

    for idx in range(1, len(sessions)):
        day, today_raw = sessions[idx]
        _, previous_raw = sessions[idx - 1]
        try:
            combined = pd.concat([previous_raw, today_raw], ignore_index=True)
            ema_frame, previous = prepare_intraday_signal_frames(
                combined,
                min_ema_bars=sp.ema_slow_period + 2,
            )
            today_only = ema_frame[
                pd.to_datetime(ema_frame["datetime"]).dt.date == day
            ].reset_index(drop=True)
            if len(today_only) < sp.ema_slow_period + 2:
                today_only = ema_frame.reset_index(drop=True)
            day_trades = replay_session(
                today_only,
                previous.reset_index(drop=True),
                instrument=instrument,
                allow_option_selling=app_settings.risk.allow_option_selling,
                bounds=bounds,
                pnl_mode=pnl_mode,
                cloud_exit=cloud_exit,
            )
        except Exception as exc:
            session_summaries.append({"session": str(day), "error": str(exc), "trades": 0})
            continue

        for t in day_trades:
            t["session"] = str(day)
            t["instrument"] = instrument.key
        all_trades.extend(day_trades)
        session_summaries.append(
            {
                "session": str(day),
                "trades": len(day_trades),
                "proxy_pnl_rupees": round(sum(float(t["proxy_pnl_rupees"]) for t in day_trades), 2),
                "gross_proxy_pnl_rupees": round(
                    sum(float(t.get("gross_proxy_pnl_rupees") or t["proxy_pnl_rupees"]) for t in day_trades),
                    2,
                ),
                "estimated_friction_rupees": round(
                    sum(float(t.get("estimated_friction_rupees") or 0) for t in day_trades), 2
                ),
            }
        )
    return all_trades, session_summaries, sessions


def run_dhan_intraday_backtest(
    client: DhanClient,
    app_settings: AppSettings,
    *,
    instrument_key: str,
    lookback_days: int = 5,
    interval: str | None = None,
    use_cache: bool = True,
    refresh_cache: bool = False,
    pnl_mode: str = "option_proxy",
    cloud_exit: bool | None = None,
) -> dict[str, Any]:
    """
    Replay strategy on Dhan / cached intraday spot candles.

    lookback_days up to 365 when cache has history; Dhan only refreshes ~5 days.
    pnl_mode: option_proxy (default) | spot
    cloud_exit: overlay an Ichimoku cloud-reentry exit on the buy lane.
      None (default) → use EXIT_BUY_ON_CLOUD_REENTRY from strategy params.
    """
    key = instrument_key.strip().upper()
    if key not in configured_index_keys():
        raise ValueError(f"{key} is not configured. Set {key}_SECURITY_ID in .env.")

    instrument = get_instrument(key)
    if instrument.underlying_security_id is None:
        raise ValueError(f"{instrument.label} security id is missing in .env.")

    pnl_mode = str(pnl_mode or "option_proxy").strip().lower()
    if pnl_mode not in {"option_proxy", "spot"}:
        pnl_mode = "option_proxy"

    cloud_exit = (
        bool(get_strategy_params().exit_buy_on_cloud_reentry)
        if cloud_exit is None
        else bool(cloud_exit)
    )

    iv = str(interval or candle_interval_minutes())
    candles, data_source = fetch_backtest_candles(
        client,
        key,
        interval=iv,
        lookback_days=lookback_days,
        use_cache=use_cache,
        refresh_cache=refresh_cache,
    )

    all_trades, session_summaries, sessions = _replay_candles(
        candles,
        instrument=instrument,
        app_settings=app_settings,
        pnl_mode=pnl_mode,
        cloud_exit=cloud_exit,
    )

    wins = sum(1 for t in all_trades if t.get("aligned"))
    cloud_exits = sum(1 for t in all_trades if t.get("exit_action") == "CLOUD_EXIT")
    total_proxy = round(sum(float(t["proxy_pnl_rupees"]) for t in all_trades), 2)
    total_gross = round(
        sum(float(t.get("gross_proxy_pnl_rupees") or t["proxy_pnl_rupees"]) for t in all_trades),
        2,
    )
    total_friction = round(sum(float(t.get("estimated_friction_rupees") or 0) for t in all_trades), 2)
    from index_ai.candle_cache import cache_status

    disclaimer = (
        "Signal replay on cached/Dhan intraday spot. "
        + (
            "option_proxy PnL uses simplified delta/theta — calibrate vs paper journal."
            if pnl_mode == "option_proxy"
            else "spot mode uses index points × lot — not option MTM."
        )
    )
    return {
        "instrument": key,
        "interval_minutes": iv,
        "lookback_days_requested": lookback_days,
        "candle_rows": len(candles),
        "sessions_replayed": max(0, len(sessions) - 1),
        "session_range": {
            "from": str(sessions[0][0]) if sessions else None,
            "to": str(sessions[-1][0]) if sessions else None,
        },
        "strategy_style": strategy_style(),
        "pnl_mode": pnl_mode,
        "cloud_exit": cloud_exit,
        "use_cache": use_cache,
        "cache": cache_status()["instruments"].get(key),
        "trades": all_trades,
        "sessions": session_summaries,
        "summary": {
            "trade_count": len(all_trades),
            "wins": wins,
            "losses": len(all_trades) - wins,
            "win_rate_pct": round(100.0 * wins / len(all_trades), 1) if all_trades else 0.0,
            "total_proxy_pnl_rupees": total_proxy,
            "gross_proxy_pnl_rupees": total_gross,
            "estimated_friction_rupees": total_friction,
            "execution_model": "next_bar_open",
            "cloud_exits": cloud_exits,
        },
        "disclaimer": disclaimer,
        "data_source": data_source,
    }


def sync_candle_cache(client: DhanClient) -> dict[str, Any]:
    """Pull latest Dhan window into memory/candles for all configured indices."""
    results = sync_all_configured(client, interval=candle_interval_minutes())
    from index_ai.candle_cache import cache_status

    return {"synced": results, "cache": cache_status()}
