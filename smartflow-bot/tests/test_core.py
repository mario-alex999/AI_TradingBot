import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
import numpy as np, pandas as pd
from bot import smc, cot
from bot.data import synthetic_ohlc


def test_swings_are_causal():
    df = synthetic_ohlc(500)
    full = smc.swings(df, 5)
    part = smc.swings(df.iloc[:300], 5)
    # swings known at bar 290 must not change when future bars are added
    assert np.allclose(full["last_sh"].iloc[:290].fillna(0), part["last_sh"].iloc[:290].fillna(0))


def test_ote_bull():
    lo_, hi_ = smc.ote_zone(hi=2.0, lo=1.0, direction=1)
    assert abs(lo_ - 1.21) < 1e-9 and abs(hi_ - 1.38) < 1e-9


def test_fvg_detect():
    df = pd.DataFrame({"open": [1, 1.1, 1.5], "high": [1.05, 1.6, 1.7], "low": [0.95, 1.0, 1.3],
                       "close": [1.0, 1.55, 1.6]}, index=pd.date_range("2024", periods=3, freq="h", tz="UTC"))
    out = smc.fair_value_gaps(df, 0)
    assert out["fvg_bull_bot"].iloc[2] == 1.05 and out["fvg_bull_top"].iloc[2] == 1.3


def test_cot_index_range_and_extremes():
    an = cot.analyze(cot.synthetic_cot(300))
    idx = an["commercials_idx_52"].dropna()
    assert idx.between(0, 100).all()
    b, why = cot.bias(an.iloc[-1])
    assert -1 <= b <= 1 and isinstance(why, list)
