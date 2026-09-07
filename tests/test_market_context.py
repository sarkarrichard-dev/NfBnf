from datetime import date

from index_ai.market_context import context as mkt
from index_ai.market_context.oi_flow import chain_oi, features as oi_features, max_pain, pinning
from index_ai.market_context.participant_oi import features as poi_features, parse_csv
from index_ai.market_context.spread_calib import calibrated_half_spread, observe, summary
from index_ai.market_context.vix import iv_term_structure, parse_all_indices

_CSV = (
    '""Participant wise Open Interest as on Aug 28, 2026"",,,,,,,,,,,,,,\n'
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts,Total Short Contracts\n"
    "Client,240128,63169,3366585,205567,3303540,2438836,3212343,3163902,1583362,661864,1011551,1002934,1,1\n"
    "DII,43377,23164,287023,4451007,4676,34529,326,40,4567,39726,211516,10497,1,1\n"
    "FII,24157,226790,3433551,2884325,536783,1018674,766803,424314,74429,163476,177472,86926,1,1\n"
    "Pro,41362,35901,806481,352741,1056433,967418,921960,871201,691079,814550,952898,579259,1,1\n"
)


def test_participant_oi_parses_net_positioning():
    p = parse_csv(_CSV, date(2026, 8, 28))
    assert p is not None
    assert p.fii_index_fut_net == 24157 - 226790     # FII net short
    assert p.client_index_fut_net == 240128 - 63169  # retail the other side
    assert p.fii_net_options_bias == p.fii_index_call_net - p.fii_index_put_net
    assert p.as_of == "2026-08-28"


def test_participant_oi_rejects_junk_and_neutral_features():
    assert parse_csv("not,a,csv", date(2026, 8, 28)) is None
    f = poi_features(None)
    assert f["participant_oi_stale"] == 1.0 and f["fii_fut_net_lakh"] == 0.0


def test_vix_regime_and_percentile():
    payload = {"data": [{"index": "INDIA VIX", "last": 10.66, "previousClose": 11.07,
                         "percentChange": -3.7, "high": 11.14, "low": 10.53,
                         "yearHigh": 28.91, "yearLow": 8.72}]}
    v = parse_all_indices(payload)
    assert v.regime == "CALM" and 0 <= v.percentile_1y <= 1
    payload["data"][0]["last"] = 27.0
    assert parse_all_indices(payload).regime == "STRESSED"
    assert parse_all_indices({"data": []}) is None


def test_iv_term_structure_shapes():
    assert iv_term_structure(20.0, 14.0)["shape"] == "BACKWARDATION"
    assert iv_term_structure(20.0, 14.0)["sell_friendly"] is False
    assert iv_term_structure(12.0, 14.0)["shape"] == "CONTANGO"
    assert iv_term_structure(14.0, 14.0)["shape"] == "FLAT"
    assert iv_term_structure(None, None)["ratio"] is None


def _rows():
    return {
        23800.0: {"ce": {"oi": 1000}, "pe": {"oi": 90000}},
        24000.0: {"ce": {"oi": 50000}, "pe": {"oi": 50000}},
        24200.0: {"ce": {"oi": 90000}, "pe": {"oi": 1000}},
    }


def test_max_pain_and_oi_walls():
    assert chain_oi(_rows())["ce"][24200.0] == 90000
    assert max_pain(_rows()) == 24000.0
    p = pinning(_rows(), 24010.0, is_expiry_day=True)
    assert p["max_call_oi_strike"] == 24200.0
    assert p["max_put_oi_strike"] == 23800.0


def test_pin_pressure_only_on_expiry_day_and_near_the_pin():
    assert pinning(_rows(), 24010.0, is_expiry_day=True)["pin_pressure"] is True
    assert pinning(_rows(), 24010.0, is_expiry_day=False)["pin_pressure"] is False
    assert pinning(_rows(), 24700.0, is_expiry_day=True)["pin_pressure"] is False
    assert oi_features(None, None)["oi_writer_bias"] == 0.0


def test_stressed_vix_blocks_selling():
    ctx = {"vix": {"last": 27.0, "regime": "STRESSED"}}
    c = mkt.conditions(ctx)
    assert c["allow_selling"] is False and c["blocks"]
    ctx["vix"]["regime"] = "CALM"
    assert mkt.conditions(ctx)["allow_selling"] is True


def test_backwardation_and_pinning_raise_warnings_not_blocks():
    ctx = {
        "vix": {"last": 11.0, "regime": "CALM"},
        "per_instrument": {"NIFTY": {
            "iv_term": iv_term_structure(20.0, 14.0),
            "pinning": {"pin_pressure": True, "max_pain": 24000, "is_expiry_day": True},
        }},
    }
    c = mkt.conditions(ctx)
    assert c["allow_selling"] is True
    assert len(c["warnings"]) == 2


def test_context_features_are_complete_and_neutral_when_missing():
    f = mkt.features(None)
    for k in ("fii_fut_net_lakh", "vix_level", "vix_pctile", "iv_term_ratio",
              "max_pain_dist_pct", "pin_pressure", "oi_writer_bias"):
        assert k in f, k
    assert f["vix_pctile"] == 0.5


class _Q:
    def __init__(self, strike, bid, ask, ltp):
        self.strike, self.bid, self.ask, self.ltp = strike, bid, ask, ltp


class _Book:
    def quote(self, strike, is_call):
        if strike == 24000:
            return _Q(24000, 119.0, 121.0, 120.0)   # 1.0pt half-spread
        if strike == 23000:
            return _Q(23000, 2.0, 2.4, 2.2)         # 0.2pt half-spread
        return None


def test_spread_observation_splits_near_and_wing(tmp_path, monkeypatch):
    monkeypatch.setattr("index_ai.market_context.spread_calib.SAMPLES_PATH", tmp_path / "s.jsonl")
    n = observe(_Book(), "NIFTY", 24010.0,
                strikes=[(24000.0, True), (23000.0, False), (55555.0, True)])
    assert n == 2
    s = summary("NIFTY")
    assert s["near"]["median_pts"] == 1.0
    assert s["wing"]["median_pts"] == 0.2
    assert s["ready"] is False


def test_env_override_beats_observation(monkeypatch):
    monkeypatch.setenv("SLIPPAGE_HALF_SPREAD_POINTS_NIFTY", "0.42")
    assert calibrated_half_spread("NIFTY") == (0.42, "env")


def test_uncalibrated_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SLIPPAGE_HALF_SPREAD_POINTS_NIFTY", raising=False)
    monkeypatch.setattr("index_ai.market_context.spread_calib.SAMPLES_PATH", tmp_path / "none.jsonl")
    hs, src = calibrated_half_spread("NIFTY")
    assert "default" in src and hs > 0
