"""Commitment of Traders (CFTC Legacy, futures-only) analytics.

Groups
  commercials  = hedgers/producers ("street money", usually the informed side at extremes)
  large_specs  = non-commercials (funds / CTAs — trend followers, right in trends, wrong at turns)
  small_specs  = non-reportables (retail; historically the weakest P&L — used as a contrarian gauge)

Signals produced
  1. COT Index per group over 52 / 156 / 208 weeks (12 months – 4 years).
  2. Extreme setup: commercials heavily net long (index >= 80) while specs heavily net short
     (index <= 20) -> bullish bias. Mirror for bearish.
  3. Street-money P&L: estimated mark-to-market P&L of each group's net position. Groups with
     positive rolling P&L are treated as "smart"; their direction is weighted up.
  4. Informed-money relationship: commercials and large specs are normally mirror images
     (corr of weekly changes ~ -0.9). When large specs START moving with commercials, or the
     commercials keep adding while price moves against them, informed money is accumulating.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import requests

CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"   # Legacy Futures Only
FIELDS = {
    "report_date_as_yyyy_mm_dd": "date",
    "open_interest_all": "oi",
    "comm_positions_long_all": "comm_long", "comm_positions_short_all": "comm_short",
    "noncomm_positions_long_all": "lspec_long", "noncomm_positions_short_all": "lspec_short",
    "nonrept_positions_long_all": "sspec_long", "nonrept_positions_short_all": "sspec_short",
}
GROUPS = {"commercials": "comm", "large_specs": "lspec", "small_specs": "sspec"}


def fetch_cot(market_substring: str, since: str = "2015-01-01") -> pd.DataFrame:
    params = {
        "$select": ",".join(FIELDS) + ",market_and_exchange_names",
        "$where": f"upper(market_and_exchange_names) like '%{market_substring.upper()}%' "
                  f"AND report_date_as_yyyy_mm_dd >= '{since}'",
        "$order": "report_date_as_yyyy_mm_dd", "$limit": 50000,
    }
    r = requests.get(CFTC_URL, params=params, timeout=60)
    r.raise_for_status()
    raw = pd.DataFrame(r.json())
    if raw.empty:
        raise ValueError(f"No COT rows for '{market_substring}'")
    market = raw["market_and_exchange_names"].mode()[0]        # keep one contract only
    raw = raw[raw["market_and_exchange_names"] == market]
    df = raw.rename(columns=FIELDS)[list(FIELDS.values())]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").astype(float).sort_index()
    df.attrs["market"] = market
    return df


def cot_index(s: pd.Series, weeks: int) -> pd.Series:
    lo, hi = s.rolling(weeks, min_periods=weeks // 2).min(), s.rolling(weeks, min_periods=weeks // 2).max()
    return (100 * (s - lo) / (hi - lo).replace(0, np.nan)).clip(0, 100)


def analyze(cot: pd.DataFrame, price: pd.Series | None = None, lookbacks=(52, 156, 208),
            hi: float = 80, lo: float = 20, contract_size: float = 1.0) -> pd.DataFrame:
    d = cot.copy()
    for g, p in GROUPS.items():
        d[f"{g}_net"] = d[f"{p}_long"] - d[f"{p}_short"]
        d[f"{g}_net_pct_oi"] = 100 * d[f"{g}_net"] / d["oi"]
        for w in lookbacks:
            d[f"{g}_idx_{w}"] = cot_index(d[f"{g}_net"], w)
        d[f"{g}_chg"] = d[f"{g}_net"].diff()
    w0 = lookbacks[0]

    # --- 2. extremes (checked on every lookback; count how many agree)
    bull = sum((d[f"commercials_idx_{w}"] >= hi) & (d[f"large_specs_idx_{w}"] <= lo) for w in lookbacks)
    bear = sum((d[f"commercials_idx_{w}"] <= lo) & (d[f"large_specs_idx_{w}"] >= hi) for w in lookbacks)
    d["extreme_bull_count"], d["extreme_bear_count"] = bull, bear
    d["small_spec_contrarian"] = np.where(d[f"small_specs_idx_{w0}"] <= lo, 1,
                                  np.where(d[f"small_specs_idx_{w0}"] >= hi, -1, 0))

    # --- 3. street-money P&L (needs weekly price aligned to report dates)
    if price is not None:
        px = price.reindex(d.index, method="ffill")
        dp = px.diff()
        for g in GROUPS:
            d[f"{g}_pnl"] = d[f"{g}_net"].shift() * dp * contract_size
            d[f"{g}_pnl_52w"] = d[f"{g}_pnl"].rolling(52, min_periods=10).sum()
        d["price"] = px

    # --- 4. informed-money relationship
    d["comm_vs_lspec_corr_26w"] = d["commercials_chg"].rolling(26, min_periods=10).corr(d["large_specs_chg"])
    d["informed_alignment"] = np.sign(d["commercials_chg"]) * (np.sign(d["commercials_chg"]) == np.sign(d["large_specs_chg"]))
    d["comm_momentum_6w"] = d[f"commercials_idx_{w0}"].diff(6)      # Williams-style movement index
    return d


def bias(row: pd.Series, lookbacks=(52, 156, 208)) -> tuple[float, list[str]]:
    """Return directional bias in [-1, 1] and human-readable reasons for the latest COT row."""
    score, why = 0.0, []
    n = len(lookbacks)
    if row["extreme_bull_count"]:
        score += 0.5 * row["extreme_bull_count"] / n
        why.append(f"Commercials heavily net LONG / large specs heavily net SHORT on {int(row['extreme_bull_count'])}/{n} lookbacks")
    if row["extreme_bear_count"]:
        score -= 0.5 * row["extreme_bear_count"] / n
        why.append(f"Commercials heavily net SHORT / large specs heavily net LONG on {int(row['extreme_bear_count'])}/{n} lookbacks")
    if row["small_spec_contrarian"]:
        score += 0.15 * row["small_spec_contrarian"]
        why.append("Small specs at extreme -> fade them" )
    mom = row.get("comm_momentum_6w", 0) or 0
    if abs(mom) >= 20:
        score += 0.2 * np.sign(mom)
        why.append(f"Commercial index moved {mom:+.0f} pts in 6 weeks (accumulation/distribution)")
    if row.get("informed_alignment", 0):
        score += 0.15 * row["informed_alignment"]
        why.append("Large specs moving WITH commercials this week (informed-money alignment)")
    # weight by who is actually making money
    if "commercials_pnl_52w" in row and not pd.isna(row["commercials_pnl_52w"]):
        smart = [g for g in GROUPS if row.get(f"{g}_pnl_52w", 0) > 0]
        why.append(f"Groups with positive 52w P&L (street money): {', '.join(smart) or 'none'}")
        if "large_specs" in smart and "commercials" not in smart:
            # trend regime: specs are winning, follow their direction lightly
            score += 0.15 * np.sign(row["large_specs_net"])
    return float(np.clip(score, -1, 1)), why


def synthetic_cot(weeks: int = 300, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    comm = np.cumsum(rng.normal(0, 4000, weeks))
    lspec = -0.85 * comm + rng.normal(0, 3000, weeks)
    sspec = -(comm + lspec)
    base = 150000
    idx = pd.date_range("2019-01-01", periods=weeks, freq="W-TUE")
    return pd.DataFrame({
        "oi": 500000.0,
        "comm_long": base + np.maximum(comm, 0), "comm_short": base + np.maximum(-comm, 0),
        "lspec_long": base + np.maximum(lspec, 0), "lspec_short": base + np.maximum(-lspec, 0),
        "sspec_long": 50000 + np.maximum(sspec, 0), "sspec_short": 50000 + np.maximum(-sspec, 0),
    }, index=idx)
