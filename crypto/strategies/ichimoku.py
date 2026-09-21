"""Ichimoku strategy for the crypto lane — reuses the index Ichimoku math.

Runs around the clock on a single timeframe (default 1h). Entry is the classic
setup: Tenkan/Kijun cross in the trade's direction, price already on the right
side of the Kumo, and the cloud coloured with the trade. Exit reuses
``index_ai.strategies.ichimoku.cloud_reentry_exit`` (price back into the cloud).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from crypto.strategies.trailing import TrailConfig, update_and_check
from index_ai.strategies.ichimoku import (
    cloud_reentry_exit,
    compute_ichimoku,
)


@dataclass(frozen=True)
class IchimokuConfig:
    conversion: int = 9
    base: int = 26
    span_b: int = 52
    displacement: int = 26
    trail: TrailConfig = field(default_factory=TrailConfig)


def _blank_state() -> dict[str, Any]:
    return {"position": None}


def step(
    symbol: str,
    candles: pd.DataFrame,
    *,
    state: dict[str, Any] | None,
    cfg: IchimokuConfig,
    live_price: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    st = {**_blank_state(), **(state or {})}
    ev: dict[str, Any] = {"strategy": "ichimoku", "asset": symbol, "event": "none"}

    need = cfg.span_b + cfg.displacement + 2
    if len(candles) < need:
        ev.update(event="wait", reason=f"need {need} bars, have {len(candles)}")
        return st, ev

    frame = compute_ichimoku(
        candles,
        conversion=cfg.conversion,
        base=cfg.base,
        span_b=cfg.span_b,
        displacement=cfg.displacement,
    )
    row, prev = frame.iloc[-1], frame.iloc[-2]
    if pd.isna(row["cloud_top"]) or pd.isna(row["senkou_b"]):
        ev.update(event="wait", reason="cloud not ready")
        return st, ev

    price = float(row["close"])
    ts = str(candles["datetime"].iloc[-1])
    # trailing stop/target reacts to the live mark, not just the last closed
    # candle — see crypto/strategies/cpr_trend.py for why. Matters even more
    # here since this lane's default timeframe is 1h.
    trail_price = live_price if live_price is not None else price
    cross_up = prev["tenkan"] <= prev["kijun"] and row["tenkan"] > row["kijun"]
    cross_dn = prev["tenkan"] >= prev["kijun"] and row["tenkan"] < row["kijun"]
    above_cloud = price > float(row["cloud_top"])
    below_cloud = price < float(row["cloud_bottom"])
    # forward Kumo colour — built from current price action, not the 26-bar-lagged
    # cloud in ``senkou_a``/``senkou_b`` (which lags a fresh trend badly).
    mid52 = (
        candles["high"].astype(float).rolling(cfg.span_b).max()
        + candles["low"].astype(float).rolling(cfg.span_b).min()
    ) / 2.0
    fwd_a = (float(row["tenkan"]) + float(row["kijun"])) / 2.0
    fwd_b = float(mid52.iloc[-1])
    bull_cloud = fwd_a > fwd_b

    pos = st["position"]

    if pos:
        side = pos["side"]
        direction = 1 if side == "long" else -1
        reason = update_and_check(pos, trail_price, cfg.trail)
        if not reason:
            should_exit, why = cloud_reentry_exit(
                direction,
                candles,
                conversion=cfg.conversion,
                base=cfg.base,
                span_b=cfg.span_b,
                displacement=cfg.displacement,
            )
            if should_exit:
                reason = why or "cloud re-entry"
        if reason:
            st["position"] = None
            ev.update(
                event="exit",
                side=side,
                price=trail_price,
                reason=reason,
                ts=ts,
                peak_pnl_pct=pos.get("peak_pnl_pct"),
                trail_stop_pnl_pct=pos.get("trail_stop_pnl_pct"),
            )
        else:
            ev.update(event="hold", side=side, price=price)
        return st, ev

    if cross_up and above_cloud and bull_cloud:
        st["position"] = {"side": "long", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter", side="long", price=price, reason="TK cross up above a bull Kumo", ts=ts
        )
    elif cross_dn and below_cloud and not bull_cloud:
        st["position"] = {"side": "short", "entry_price": price, "entry_time": ts}
        ev.update(
            event="enter",
            side="short",
            price=price,
            reason="TK cross down below a bear Kumo",
            ts=ts,
        )
    else:
        ev.update(event="wait", reason="no Ichimoku entry")
    return st, ev


if (
    __name__ == "__main__"
):  # self-check — a dip then a rally: TK crosses up above the (lagged) cloud
    n = 160
    closes = [120 - 0.15 * i for i in range(90)] + [106.5 + 1.2 * i for i in range(70)]
    closes = closes[:n]
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-08-01", periods=n, freq="1h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [5.0] * n,
        }
    )
    cfg = IchimokuConfig()
    state, saw_enter = None, False
    for i in range(cfg.span_b + cfg.displacement + 3, n):
        state, ev = step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] == "enter":
            saw_enter = ev["side"] == "long"
            break
    assert saw_enter, "expected a long entry on the synthetic uptrend"
    print("crypto.strategies.ichimoku self-check ok")
