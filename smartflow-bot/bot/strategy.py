"""SMARTFLOW CONFLUENCE MODEL (SFCM) — the bot's unique method.

Top-down, 7-step pipeline. Every step contributes points to a 0–100 score; only setups
>= min_score are traded. The score breakdown is stored with each signal so the learning
loop can re-weight components that actually produce winners.

 1. COT BIAS (weekly)   commercials vs specs extremes, street-money P&L, informed alignment
 2. HTF STRUCTURE       daily trend (BOS/CHoCH) agrees with COT bias
 3. SESSION / KILLZONE  only London or New York killzone; Asian range = liquidity pool
 4. LIQUIDITY SWEEP     Judas swing: price raids Asian/swing liquidity AGAINST the bias
 5. DISPLACEMENT        CHoCH/BOS on setup TF back IN the bias direction (smart money reveals)
 6. POI ENTRY           limit at OTE (62–79%, 70.5% sweet spot) overlapping an unmitigated
                        order block and/or FVG, in DISCOUNT for longs / PREMIUM for shorts
 7. RISK & TARGET       SL beyond sweep extreme + ATR buffer; TP = opposing external
                        liquidity / S&R; reject if RR < min_rr
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from collections import Counter
import numpy as np
import pandas as pd
from . import smc
from .levels import sr_levels, near_level
from .sessions import active_session


@dataclass
class Setup:
    time: str
    direction: int
    entry: float
    stop: float
    target: float
    rr: float
    score: float
    breakdown: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    session: str | None = None
    bar: int = -1

    def to_dict(self):
        return asdict(self)


def htf_trend(df_setup: pd.DataFrame, swing_len: int = 3) -> pd.Series:
    """Daily structure trend, shifted one day (only completed days are used -> no look-ahead)."""
    daily = df_setup[["open", "high", "low", "close"]].resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    d = smc.market_structure(smc.swings(daily, swing_len))
    t = d["trend"].shift(1)
    return t.reindex(df_setup.index, method="ffill").fillna(0)


def cot_bias_series(cot_an: pd.DataFrame | None, index: pd.DatetimeIndex, bias_fn) -> pd.Series:
    """COT data is as-of Tuesday, released Friday 15:30 ET -> available from the next bar after release."""
    if cot_an is None or cot_an.empty:
        return pd.Series(0.0, index=index)
    vals = pd.Series([bias_fn(r)[0] for _, r in cot_an.iterrows()], index=cot_an.index + pd.Timedelta(days=3, hours=21))
    if vals.index.tz is None:
        vals.index = vals.index.tz_localize("UTC")
    return vals.reindex(index, method="ffill").fillna(0.0)


class SmartFlowStrategy:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.w = cfg["scoring"]
        self.smc_cfg = cfg["smc"]
        self.risk = cfg["risk"]
        self.trade_sessions = set(cfg["sessions"].get("trade_only_in", ["london", "newyork"]))

    def prepare(self, d: pd.DataFrame, cot_bias: pd.Series):
        self.d = d
        self.d["cot_bias"] = cot_bias
        self.d["htf_trend"] = htf_trend(d)
        self.obs = smc.order_blocks(d, self.smc_cfg["ob_lookback"])
        self.fvgs = smc.fvg_zones(d)
        self._used_legs = set()
        self.rejections = Counter()

    def _rej(self, why):
        self.rejections[why] += 1
        return None

    def evaluate(self, i: int, window: int = 24) -> Setup | None:
        d, w = self.d, self.w
        if i < 100:
            return self._rej("warmup")
        row = d.iloc[i]
        sess = active_session(row)
        if sess not in self.trade_sessions:
            return self._rej("session")

        # 1+2 directional bias
        cb, ht = row["cot_bias"], row["htf_trend"]
        combo = 0.6 * cb + 0.4 * ht
        direction = int(np.sign(combo)) if abs(combo) >= 0.15 else int(ht)
        if direction == 0:
            return self._rej("no_bias")
        bd, why = {}, []
        if np.sign(cb) == direction and abs(cb) >= 0.25:
            bd["cot_bias"] = w["cot_bias"] * min(1, abs(cb) / 0.6); why.append(f"COT bias {cb:+.2f}")
        if ht == direction:
            bd["htf_structure"] = w["htf_structure"]; why.append("Daily structure aligned")

        recent = d.iloc[i - window:i]
        # 4 liquidity sweep against bias
        sweep_col = "sweep_low" if direction > 0 else "sweep_high"
        sweeps = np.nonzero(recent[sweep_col].values)[0]
        if not len(sweeps):
            return self._rej("no_sweep")
        sweep_i = i - window + sweeps[-1]
        # 5 displacement: structure break in bias direction AFTER sweep
        after = d.iloc[sweep_i:i]
        brk = ((after["choch"] == direction) | (after["bos"] == direction)).values
        if not brk.any():
            return self._rej("no_displacement")
        bd["liquidity_sweep"] = w["liquidity_sweep"]; why.append("Liquidity sweep + displacement (CHoCH/BOS)")

        # impulse leg + OTE
        if direction > 0:
            leg_lo = d["low"].iloc[sweep_i - 2:i].min(); leg_hi = after["high"].max()
        else:
            leg_hi = d["high"].iloc[sweep_i - 2:i].max(); leg_lo = after["low"].min()
        leg_key = (direction, round(leg_lo, 6), round(leg_hi, 6))
        if leg_key in self._used_legs or leg_hi <= leg_lo:
            return self._rej("leg_used")
        ote_lo, ote_hi = smc.ote_zone(leg_hi, leg_lo, direction, self.smc_cfg["ote_low"], self.smc_cfg["ote_high"])
        sweet = leg_hi - 0.705 * (leg_hi - leg_lo) if direction > 0 else leg_lo + 0.705 * (leg_hi - leg_lo)

        # 6 POI overlap
        kinds = ("bull_ob", "bull_fvg") if direction > 0 else ("bear_ob", "bear_fvg")
        zones = [z for z in smc.active_zones(self.obs + self.fvgs, d, i) if z.kind in kinds
                 and z.bottom <= ote_hi and z.top >= ote_lo]
        entry = sweet
        if zones:
            if any(z.kind.endswith("ob") for z in zones):
                bd["order_block"] = w["order_block"]; why.append("Unmitigated order block inside OTE")
            if any(z.kind.endswith("fvg") for z in zones):
                bd["fvg"] = w["fvg"]; why.append("Fair value gap inside OTE")
            z = zones[-1]
            entry = float(np.clip(sweet, z.bottom, z.top))
        if not (row["low"] <= entry <= row["high"]):
            return self._rej("poi_not_reached")  # price has not reached the POI
        pd_pos = smc.premium_discount(entry, *smc.dealing_range(d, i))
        if (direction > 0 and pd_pos < 0.5) or (direction < 0 and pd_pos > 0.5):
            bd["ote_zone"] = w["ote_zone"]; why.append(f"OTE in {'discount' if direction > 0 else 'premium'} ({pd_pos:.2f})")

        a = row["atr"]
        lv = sr_levels(d, i, self.cfg["levels"]["pivot_window"], self.cfg["levels"]["cluster_atr"])
        if near_level(entry, lv, 0.5 * a):
            bd["sr_level"] = w["sr_level"]; why.append("Entry on multi-touch S/R")
        bd["session"] = w["session"]; why.append(f"{sess.title()} killzone")

        # 7 risk / target
        buf = self.risk["sl_buffer_atr"] * a
        stop = leg_lo - buf if direction > 0 else leg_hi + buf
        risk = abs(entry - stop)
        if risk <= 0:
            return self._rej("bad_risk")
        hist = d.iloc[max(0, i - 150):i]
        if direction > 0:
            cands = [x["level"] for x in lv if x["level"] > entry + risk * self.risk["min_rr"]] + [hist["high"].max()]
            target = min(c for c in cands if c > entry) if any(c > entry for c in cands) else entry + 2 * risk
        else:
            cands = [x["level"] for x in lv if x["level"] < entry - risk * self.risk["min_rr"]] + [hist["low"].min()]
            target = max(c for c in cands if c < entry) if any(c < entry for c in cands) else entry - 2 * risk
        rr = abs(target - entry) / risk
        if rr < self.risk["min_rr"]:
            return self._rej("rr_too_low")
        score = float(sum(bd.values()))
        if score < w["min_score"]:
            return self._rej("score_too_low")
        self._used_legs.add(leg_key)
        return Setup(str(d.index[i]), direction, float(entry), float(stop), float(target), round(rr, 2),
                     round(score, 1), {k: round(v, 1) for k, v in bd.items()}, why, sess, i)
