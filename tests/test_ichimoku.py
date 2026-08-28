import numpy as np
import pandas as pd

from index_ai.ichimoku import cloud_reentry_exit, compute_ichimoku, ichimoku_snapshot


def _frame(closes: list[float]) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-08-05 09:15", periods=len(closes), freq="1min"),
            "open": closes,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": 1_000.0,
        }
    )


def test_cloud_is_forward_displaced_and_non_repainting():
    frame = _frame(list(np.linspace(100, 200, 120)))
    ich = compute_ichimoku(frame, conversion=9, base=26, span_b=52, displacement=26)
    # senkou_a needs tenkan(9)+kijun(26) then shift(26) -> ready at index 51
    assert ich["senkou_a"].iloc[:51].isna().all()
    assert ich["senkou_a"].iloc[51:].notna().all()
    # senkou_b needs span_b(52) then shift(26) -> ready at index 77
    assert ich["senkou_b"].iloc[:77].isna().all()
    assert ich["senkou_b"].iloc[77:].notna().all()
    # row i cloud == raw span computed at row i-26 (forward displacement, non-repainting)
    raw_a = (
        (frame["high"].rolling(9).max() + frame["low"].rolling(9).min()) / 2
        + (frame["high"].rolling(26).max() + frame["low"].rolling(26).min()) / 2
    ) / 2
    assert ich["senkou_a"].iloc[100] == raw_a.iloc[74]


def test_snapshot_reports_position_relative_to_cloud():
    up = _frame(list(np.linspace(100, 260, 140)))
    snap = ichimoku_snapshot(up)
    assert snap.ready and snap.position == "above"


def test_not_ready_never_exits():
    hit, reason = cloud_reentry_exit(1, _frame(list(range(100, 130))))
    assert hit is False and reason is None


def test_long_exits_when_price_falls_back_into_cloud():
    closes = list(np.linspace(100, 250, 120)) + list(np.linspace(250, 150, 40))
    hit, reason = cloud_reentry_exit(1, _frame(closes), long_ref="cloud_top")
    assert hit is True
    assert reason is not None


def test_long_holds_while_price_stays_above_cloud():
    closes = list(np.linspace(100, 300, 160))
    hit, _ = cloud_reentry_exit(1, _frame(closes))
    assert hit is False


def test_short_exits_when_price_reclaims_kijun():
    closes = list(np.linspace(300, 120, 120)) + list(np.linspace(120, 210, 40))
    hit, reason = cloud_reentry_exit(-1, _frame(closes), short_ref="kijun")
    assert hit is True
    assert reason is not None


def test_direction_zero_is_a_noop():
    assert cloud_reentry_exit(0, _frame(list(np.linspace(100, 200, 120)))) == (False, None)
