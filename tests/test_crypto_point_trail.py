from crypto.strategies.trailing import TrailConfig, bracket_stop_price, update_and_check

CFG = TrailConfig(point_trail_pct=1.6)   # BTC entry 84,000 -> 1,344 pts


def test_btc_long_stop_starts_1344_below_and_follows_one_for_one():
    pos = {"entry_price": 84000.0, "side": "long"}
    assert update_and_check(pos, 84000.0, CFG) is None and pos["trail_stop_price"] == 84000 - 1344
    assert update_and_check(pos, 85000.0, CFG) is None and pos["trail_stop_price"] == 85000 - 1344
    assert update_and_check(pos, 84500.0, CFG) is None                  # pullback, stop holds
    reason = update_and_check(pos, 83650.0, CFG)                        # through 83,656
    assert reason and "point trail" in reason and "-344 pts from entry" in reason


def test_short_mirrors_and_exchange_backstop_sits_at_the_initial_stop():
    pos = {"entry_price": 84000.0, "side": "short"}
    update_and_check(pos, 84000.0, CFG)
    assert pos["trail_stop_price"] == 84000 + 1344
    assert bracket_stop_price(84000.0, "short", CFG) == 85344.0


def test_zero_keeps_the_old_pnl_percent_trail():
    pos = {"entry_price": 100.0, "side": "long"}
    assert update_and_check(pos, 100.0, TrailConfig()) is None and "best_price" not in pos
