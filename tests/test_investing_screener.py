from investing import openbb_bridge, screener, universe


def test_screener_ranks_cheap_healthy_names_above_expensive_indebted_ones(monkeypatch):
    fake = {
        "GOODCO": {
            "pe_ratio": 12.0,
            "debt_to_equity": 20.0,
            "profit_margin": 0.18,
            "revenue_growth": 0.15,
        },
        "BADCO": {
            "pe_ratio": 60.0,
            "debt_to_equity": 200.0,
            "profit_margin": 0.02,
            "revenue_growth": -0.1,
        },
        "NODATA": {"error": "no data"},
    }
    monkeypatch.setattr(openbb_bridge, "fetch_fundamentals", lambda symbols, **_: fake)
    monkeypatch.setattr(universe, "load_or_fetch_universe", lambda **_: dict.fromkeys(fake, {}))

    rows = screener.run_screen()

    assert [r["symbol"] for r in rows] == ["GOODCO", "BADCO"]
    assert rows[0]["score"] > rows[1]["score"]


def test_screener_skips_rows_with_no_pe_or_margin_data():
    assert screener._score({"pe_ratio": None, "profit_margin": 0.1}) is None
    assert screener._score({"pe_ratio": 15.0, "profit_margin": None}) is None
    assert screener._score({"pe_ratio": 15.0, "profit_margin": 0.1}) is not None
