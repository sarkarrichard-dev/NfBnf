"""The 3 video strategy ports — a synthetic path fires enter/exit for each."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto.strategies import (
    ak_roxx_pro,
    bb_reversal,
    ema_jaguar,
    tma_phoenix,
    vp_edge,
)
from crypto.strategies.volprofile import profile


def _run(module, cfg, df, start):
    state, saw = None, {"enter": 0, "exit": 0}
    first_side = None
    for i in range(start, len(df)):
        state, ev = module.step("BTCUSD", df.iloc[: i + 1], state=state, cfg=cfg)
        if ev["event"] in saw:
            saw[ev["event"]] += 1
            if ev["event"] == "enter" and first_side is None:
                first_side = ev["side"]
    return saw, first_side


def test_ema_jaguar_crosses_both_ways():
    closes = (
        [100 + 0.5 * i for i in range(40)]
        + [120 - 0.7 * i for i in range(40)]
        + [92 + 0.6 * i for i in range(40)]
    )
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=len(closes), freq="5min", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [5.0] * len(closes),
        }
    )
    saw, side = _run(ema_jaguar, ema_jaguar.EmaJaguarConfig(fast=5, slow=13), df, 16)
    assert saw["enter"] >= 2 and saw["exit"] >= 1
    assert side == "short"  # the fall crosses down first


def test_bb_reversal_fires_a_long_on_a_band_pierce_reclaim():
    rng = np.random.default_rng(2)
    closes = (
        list(100 + rng.normal(0, 0.8, 20))
        + [98, 95, 91, 87, 90, 93, 91, 88, 92, 96, 100, 104]
        + [104.0] * 6
    )
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=len(closes), freq="5min", tz="UTC"),
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [5.0] * len(closes),
        }
    )
    cfg = bb_reversal.BBReversalConfig(bb_len=14, bb_dev=2.0, swing_left=2, swing_right=1)
    saw, side = _run(bb_reversal, cfg, df, 16)
    assert saw["enter"] >= 1 and side == "long"


def test_bb_reversal_skips_a_dead_flat_market():
    closes = [100.0] * 60
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=60, freq="5min", tz="UTC"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [5.0] * 60,
        }
    )
    saw, _ = _run(bb_reversal, bb_reversal.BBReversalConfig(bb_len=14), df, 16)
    assert saw["enter"] == 0


def test_vp_edge_fades_a_balanced_range():
    rng = np.random.default_rng(3)
    n = 400
    px = np.empty(n)
    px[0] = 100.0
    for i in range(1, n):
        px[i] = px[i - 1] + 0.15 * (100.0 - px[i - 1]) + rng.normal(0, 0.35)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC"),
            "open": px,
            "high": px + 0.3,
            "low": px - 0.3,
            "close": px,
            "volume": np.abs(rng.normal(10, 2, n)),
        }
    )
    cfg = vp_edge.VpEdgeConfig(vp_lookback=100, vp_bins=24, edge_buffer_pct=1.0)
    saw, _ = _run(vp_edge, cfg, df, 102)
    assert saw["enter"] >= 1 and saw["exit"] >= 1

    # a one-way ramp is not balanced → no entries
    ramp = np.linspace(100, 160, n)
    df2 = df.assign(open=ramp, high=ramp + 0.3, low=ramp - 0.3, close=ramp)
    saw2, _ = _run(vp_edge, cfg, df2, 102)
    assert saw2["enter"] == 0


def test_volprofile_value_area_ordering():
    rng = np.random.default_rng(0)
    px = 100 + rng.normal(0, 1.0, 300)
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-01", periods=300, freq="15min", tz="UTC"),
            "high": px + 0.3,
            "low": px - 0.3,
            "close": px,
            "open": px,
            "volume": np.abs(rng.normal(10, 2, 300)),
        }
    )
    p = profile(df)
    assert p and p.val < p.poc < p.vah and p.balanced


def test_ak_roxx_pro_enters_on_a_fresh_confluence_beyond_the_hourly_cpr():
    # base chop -> rally -> pullback -> resumed rally: edge-triggered, so it
    # fires on the resume, not on every bar the confluence stays true.
    df = ak_roxx_pro._demo_frame()
    saw, side = _run(ak_roxx_pro, ak_roxx_pro.AkRoxxConfig(), df, 560)
    assert saw["enter"] >= 1 and side == "long"

    # dead-flat tape: the 8-condition gate never opens
    flat = df.assign(open=100.0, high=100.3, low=99.7, close=100.0)
    saw2, _ = _run(ak_roxx_pro, ak_roxx_pro.AkRoxxConfig(), flat, 400)
    assert saw2["enter"] == 0

    # a smooth one-way ramp is always in confluence -> never a fresh signal
    n = len(df)
    ramp = 100.0 + np.linspace(0, 80, n) ** 1.15
    smooth = df.assign(open=ramp, high=ramp + 0.1, low=ramp - 0.1, close=ramp)
    saw3, _ = _run(ak_roxx_pro, ak_roxx_pro.AkRoxxConfig(), smooth, 500)
    assert saw3["enter"] == 0


def test_tma_phoenix_enters_on_a_reversal_candle_with_the_smma_ribbon():
    n = 420
    idx = pd.date_range("2026-09-06 00:00", periods=n, freq="5min", tz="UTC")
    px = np.concatenate([100.0 + np.linspace(0, 2, 150), np.linspace(102.0, 140.0, n - 150)])
    o, c, hi, lo = px.copy(), px.copy(), px + 0.3, px - 0.3
    o[360], c[360], hi[360], lo[360] = (
        px[360] + 0.25,
        px[360] - 0.25,
        px[360] + 0.35,
        px[360] - 0.35,
    )
    o[361], c[361], hi[361], lo[361] = (
        px[360] - 0.30,
        px[360] + 0.45,
        px[360] + 0.55,
        px[360] - 0.40,
    )
    df = pd.DataFrame(
        {"datetime": idx, "open": o, "high": hi, "low": lo, "close": c, "volume": [10.0] * n}
    )
    cfg = tma_phoenix.TmaPhoenixConfig(slope_lookback=3, require_ma3=False)
    saw, side = _run(tma_phoenix, cfg, df, 300)
    assert saw["enter"] >= 1 and side == "long"

    flat = df.assign(open=100.0, high=100.4, low=99.6, close=100.0)
    saw2, _ = _run(tma_phoenix, cfg, flat, 300)
    assert saw2["enter"] == 0
