import json

from index_ai import daily_ops


def test_sampling_defaults_to_all_three_indices(monkeypatch):
    monkeypatch.delenv("SPREAD_SAMPLE_INSTRUMENTS", raising=False)
    assert daily_ops.sampling_instruments() == ["NIFTY", "BANKNIFTY", "SENSEX"]
    monkeypatch.setenv("SPREAD_SAMPLE_INSTRUMENTS", "NIFTY, SENSEX")
    assert daily_ops.sampling_instruments() == ["NIFTY", "SENSEX"]


def test_sampling_on_by_default_and_opt_out(monkeypatch):
    monkeypatch.delenv("ENABLE_SPREAD_SAMPLING", raising=False)
    assert daily_ops.sample_spreads_enabled() is True
    monkeypatch.setenv("ENABLE_SPREAD_SAMPLING", "false")
    assert daily_ops.sample_spreads_enabled() is False


def test_sample_spreads_never_raises_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SPREAD_SAMPLING", "false")
    out = daily_ops.sample_spreads(object())
    assert out["skipped"] == "sampling disabled"
    assert out["sampled"] == {}


def test_sample_spreads_survives_a_broken_client(monkeypatch):
    monkeypatch.setenv("ENABLE_SPREAD_SAMPLING", "true")
    monkeypatch.setattr("index_ai.market_clock.is_market_open", lambda *a, **k: True)

    class Boom:
        def expiry_list(self, *a):
            raise RuntimeError("broker down")

    out = daily_ops.sample_spreads(Boom())
    # every index records an error, nothing propagates
    assert out["sampled"] and all("error" in v for v in out["sampled"].values())


def test_eod_runs_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_ops, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(daily_ops, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(daily_ops, "eod_due", lambda: True)
    r = daily_ops.run_eod()
    assert r["date"] and "brain" in r and "spreads" in r and "viability" in r
    # state now records today, so the real eod_due would return False
    assert json.loads((tmp_path / "s.json").read_text())["eod_date"] == r["date"]
    assert (tmp_path / "reports" / f"{r['date']}.md").is_file()


def test_report_markdown_states_verdicts_plainly():
    md = daily_ops.render_markdown({
        "date": "2026-08-31",
        "spreads": {"instruments": {"NIFTY": {"samples": 40, "source": "observed (40 samples)",
                                              "near": {"median_pts": 0.2},
                                              "wing": {"median_pts": 0.6}}}},
        "viability": {"instruments": {"BANKNIFTY": {"sell": {
            "friction_floor_rupees": 589, "gross_per_trade_rupees": 229,
            "verdict": "NOT_VIABLE"}}}},
        "brain": {"trained": False, "reason": "not enough rows"},
    })
    assert "NOT_VIABLE" in md and "not enough rows" in md and "0.2pt" in md


def test_latest_report_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_ops, "REPORT_DIR", tmp_path / "nope")
    assert daily_ops.latest_report() is None
