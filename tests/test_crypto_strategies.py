"""The 3 video strategy ports — a synthetic path fires enter/exit for each."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto.strategies import bb_reversal, ema_jaguar, fvg_scalp, vp_edge
from crypto.strategies.fairvalue import find_fvgs
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


def test_fvg_scalp_enters_on_a_gap_retest_then_trails_out():
    # slow uptrend, an impulse leg that leaves a bullish FVG, a retrace into it
    c = [100.0 + 0.1 * i for i in range(40)] + [104.0, 105.0, 111.0, 111.5, 112.0]
    c += [111.0 - 0.55 * i for i in range(1, 11)] + [105.6] * 15
    n = len(c)
    o = [x - 0.1 for x in c]
    hi = [x + 0.5 for x in c]
    lo = [x - 0.5 for x in c]
    vol = [10.0] * n
    hi[42], lo[42], vol[42] = 111.8, 104.9, 90.0
    lo[43] = 110.6
    o[55], c[55], hi[55], lo[55] = 106.0, 106.4, 106.7, 104.4  # hammer in the gap
    df = pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-09-08 14:00", periods=n, freq="5min", tz="Asia/Kolkata"
            ),
            "open": o,
            "high": hi,
            "low": lo,
            "close": c,
            "volume": vol,
        }
    )
    cfg = fvg_scalp.FvgScalpConfig(
        atr_len=10,
        vol_lookback=10,
        struct_left=3,
        struct_right=2,
        ema_fast=10,
        stretch_atr=0.3,
        fvg_min_atr=0.1,
    )
    saw, first = _run(fvg_scalp, cfg, df, 30)
    assert saw["enter"] >= 1
    assert first == "long"

    # outside the IST session window → no entries at all
    off = df.assign(
        datetime=pd.date_range("2026-09-08 03:00", periods=n, freq="5min", tz="Asia/Kolkata")
    )
    saw2, _ = _run(fvg_scalp, cfg, off, 30)
    assert saw2["enter"] == 0


def test_fvg_detection_drops_a_gap_once_price_closes_through_it():
    hi = [10.0, 10, 10, 11, 13, 15, 16, 16, 16, 16]
    lo = [9.0, 9, 9, 10, 12, 14, 15, 15, 15, 15]
    cl = [9.5, 9.5, 9.5, 10.5, 12.5, 14.5, 15.5, 15.5, 15.5, 15.5]
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-09-08", periods=10, freq="5min", tz="UTC"),
            "open": cl,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": [1.0] * 10,
        }
    )
    assert any(g["dir"] == 1 for g in find_fvgs(frame, atr_val=1.0, min_atr=0.2))
    frame.loc[9, ["close", "low"]] = [8.0, 7.5]
    assert not any(g["dir"] == 1 for g in find_fvgs(frame, atr_val=1.0, min_atr=0.2))


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
