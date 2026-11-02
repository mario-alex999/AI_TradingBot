"""Market data quality validation.

Nothing is deleted or modified here. Every candle gets ``quality_status``
(valid / suspect / invalid) and ``quality_flags``; every finding becomes a :class:`QualityIssue`
that is logged and stored in ``data_quality_issues``.

Gap classification: a missing candle period is an *expected market closure* when the market is
closed for at least half of that period according to the instrument's ``trading_hours``
(weekend, daily break, holiday). Otherwise it is an *unexpected gap*.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from aurum.config import TIMEFRAME_SECONDS, TradingHours

log = logging.getLogger(__name__)

ERROR, WARNING, INFO = "error", "warning", "info"
PRICE_COLS = ["open", "high", "low", "close"]
MAX_ENUMERATED_MISSING = 2000


@dataclass
class QualityIssue:
    issue_type: str
    severity: str
    ts_utc: datetime | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    candles: pd.DataFrame                    # sorted, de-duplicated for storage, with quality columns
    issues: list[QualityIssue]
    duplicates: pd.DataFrame                 # duplicate rows kept aside for investigation

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.issue_type] = out.get(i.issue_type, 0) + 1
        return out

    @property
    def has_errors(self) -> bool:
        return any(i.severity == ERROR for i in self.issues)


# --------------------------------------------------------------------------- market hours
def is_market_open(ts: datetime, hours: TradingHours) -> bool:
    """True if the market is open at UTC instant ``ts`` according to ``hours``.

    ``ts`` is converted to ``hours.timezone`` first, so schedules in an exchange's local time
    follow daylight-saving changes. Naive ``ts`` values are treated as UTC.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(hours.tz)
    if ts.date() in hours.holidays:
        return False
    minute_of_week = ts.weekday() * 1440 + ts.hour * 60 + ts.minute
    close_m = hours.weekly_close_day * 1440 + hours.weekly_close_time.hour * 60 + hours.weekly_close_time.minute
    open_m = hours.weekly_open_day * 1440 + hours.weekly_open_time.hour * 60 + hours.weekly_open_time.minute
    if close_m < open_m:
        if close_m <= minute_of_week < open_m:
            return False
    elif minute_of_week >= close_m or minute_of_week < open_m:
        return False
    t = ts.time()
    for start, end in hours.daily_breaks:
        if (start <= t < end) if start < end else (t >= start or t < end):
            return False
    return True


def period_is_closure(start: datetime, seconds: int, hours: TradingHours) -> bool:
    """True when the market is closed for at least half of [start, start+seconds)."""
    samples = max(1, min(48, seconds // 300))
    step = seconds / samples
    closed = sum(not is_market_open(start + timedelta(seconds=step * (k + 0.5)), hours) for k in range(samples))
    return closed / samples >= 0.5


# --------------------------------------------------------------------------- validator
class ValidationService:
    def __init__(self, gap_range_multiple: float = 8.0, range_window: int = 50):
        self.gap_range_multiple = gap_range_multiple
        self.range_window = range_window

    def validate(self, candles: pd.DataFrame, timeframe: str, hours: TradingHours,
                 symbol: str | None = None) -> ValidationResult:
        issues: list[QualityIssue] = []
        if candles is None or candles.empty:
            issues.append(QualityIssue("no_data", ERROR, None,
                                       {"symbol": symbol, "timeframe": timeframe,
                                        "message": "MT5 returned no candles for this instrument/timeframe"}))
            return ValidationResult(pd.DataFrame(columns=list(candles.columns) if candles is not None else []),
                                    issues, pd.DataFrame())

        df = candles.copy().reset_index(drop=True)
        df["received_pos"] = np.arange(len(df))
        flags: list[list[str]] = [[] for _ in range(len(df))]
        status = np.array(["valid"] * len(df), dtype=object)

        def mark(idx: int, flag: str, severity: str) -> None:
            flags[idx].append(flag)
            if severity == ERROR:
                status[idx] = "invalid"
            elif severity == WARNING and status[idx] == "valid":
                status[idx] = "suspect"

        def row_details(i: int) -> dict[str, Any]:
            r = df.iloc[i]
            return {"received_pos": int(r["received_pos"]), "mt5_time": _py(r.get("mt5_time")),
                    **{c: _py(r.get(c)) for c in PRICE_COLS}}

        # 1. missing timestamps
        missing_ts = df["ts_utc"].isna().to_numpy()
        for i in np.nonzero(missing_ts)[0]:
            issues.append(QualityIssue("missing_timestamp", ERROR, None, row_details(i)))

        # 2. missing values
        nan_mask = df[PRICE_COLS].isna()
        for i in np.nonzero(nan_mask.any(axis=1).to_numpy())[0]:
            cols = [c for c in PRICE_COLS if nan_mask.iat[i, PRICE_COLS.index(c)]]
            mark(i, "missing_values", ERROR)
            issues.append(QualityIssue("missing_values", ERROR, _ts(df, i), {**row_details(i), "columns": cols}))

        # 3. non-positive prices
        nonpos = (df[PRICE_COLS] <= 0).any(axis=1).to_numpy()
        for i in np.nonzero(nonpos)[0]:
            mark(i, "non_positive_price", ERROR)
            issues.append(QualityIssue("non_positive_price", ERROR, _ts(df, i), row_details(i)))

        # 4. OHLC relationships
        o, h, l, c = (df[k].to_numpy(dtype="float64") for k in PRICE_COLS)
        with np.errstate(invalid="ignore"):
            bad_high = h < np.fmax(np.fmax(o, c), l)
            bad_low = l > np.fmin(np.fmin(o, c), h)
        for i in np.nonzero(bad_high | bad_low)[0]:
            mark(i, "invalid_ohlc", ERROR)
            rule = []
            if bad_high[i]: rule.append("high < max(open, close, low)")
            if bad_low[i]: rule.append("low > min(open, close, high)")
            issues.append(QualityIssue("invalid_ohlc", ERROR, _ts(df, i), {**row_details(i), "rule": rule}))

        # 5. negative volume / spread
        for col in ("tick_volume", "real_volume", "spread"):
            if col in df:
                neg = (pd.to_numeric(df[col], errors="coerce") < 0).to_numpy()
                for i in np.nonzero(neg)[0]:
                    mark(i, f"negative_{col}", WARNING)
                    issues.append(QualityIssue(f"negative_{col}", WARNING, _ts(df, i), row_details(i)))

        # 6. incomplete candles
        if "is_complete" in df:
            for i in np.nonzero(~df["is_complete"].astype(bool).to_numpy())[0]:
                flags[i].append("incomplete")
                issues.append(QualityIssue("incomplete_candle", INFO, _ts(df, i),
                                           {**row_details(i), "message": "candle still forming"}))

        df["quality_status"] = status
        df["quality_flags"] = [",".join(f) for f in flags]

        # 7. ordering (MT5 should return oldest -> newest)
        valid_ts = df.loc[~missing_ts, "ts_utc"]
        inversions = int((valid_ts.diff() < pd.Timedelta(0)).sum())
        if inversions:
            issues.append(QualityIssue("unordered_timestamps", WARNING, None,
                                       {"inversions": inversions, "message": "candles were sorted by ts_utc"}))

        # 8. duplicates (same timestamp)
        timed = df.loc[~missing_ts].sort_values(["ts_utc", "received_pos"], kind="stable")
        dup_mask = timed.duplicated("ts_utc", keep=False)
        duplicates = timed.loc[dup_mask].copy()
        for ts, grp in duplicates.groupby("ts_utc"):
            identical = len(grp[PRICE_COLS].drop_duplicates()) == 1
            sev = WARNING if identical else ERROR
            issues.append(QualityIssue("duplicate_candle" if identical else "conflicting_duplicate", sev,
                                       ts.to_pydatetime(),
                                       {"copies": len(grp), "kept_received_pos": int(grp["received_pos"].iloc[0]),
                                        "rows": [{k: _py(v) for k, v in r.items() if k in PRICE_COLS + ["received_pos"]}
                                                 for r in grp.to_dict("records")]}))
        clean = timed.drop_duplicates("ts_utc", keep="first").copy()
        if not duplicates.empty:
            first_idx = clean.index.intersection(duplicates.index)
            clean.loc[first_idx, "quality_status"] = clean.loc[first_idx, "quality_status"].replace("valid", "suspect")
            clean.loc[first_idx, "quality_flags"] = clean.loc[first_idx, "quality_flags"].map(
                lambda f: ",".join([x for x in [f, "duplicate"] if x]))

        # 9. gaps: expected closures vs unexpected missing periods
        issues += self._gap_issues(clean, timeframe, hours)

        # 10. abnormal price jumps between consecutive candles
        issues += self._jump_issues(clean, timeframe, hours)
        for iss in issues:
            if iss.issue_type == "abnormal_gap" and iss.ts_utc is not None:
                m = clean["ts_utc"] == pd.Timestamp(iss.ts_utc)
                clean.loc[m & (clean["quality_status"] == "valid"), "quality_status"] = "suspect"
                clean.loc[m, "quality_flags"] = clean.loc[m, "quality_flags"].map(
                    lambda f: ",".join([x for x in [f, "abnormal_gap"] if x]))

        clean = clean.reset_index(drop=True)
        for iss in issues:
            if iss.severity != INFO:
                log.warning("Data quality issue: %s", iss.issue_type,
                            extra={"symbol": symbol, "timeframe": timeframe, "ts_utc": str(iss.ts_utc),
                                   "severity": iss.severity})
        return ValidationResult(clean, issues, duplicates)

    def _gap_issues(self, df: pd.DataFrame, timeframe: str, hours: TradingHours) -> list[QualityIssue]:
        out: list[QualityIssue] = []
        if len(df) < 2:
            return out
        secs = TIMEFRAME_SECONDS[timeframe]
        step = pd.Timedelta(seconds=secs)
        ts = df["ts_utc"].reset_index(drop=True)
        diffs = ts.diff()
        for i in np.nonzero((diffs > step).to_numpy())[0]:
            prev, nxt = ts.iloc[i - 1], ts.iloc[i]
            # tolerance absorbs 23h/25h D1 spacing around broker DST changes
            n_missing = int(np.floor((nxt - prev + 0.1 * step) / step)) - 1
            if n_missing <= 0:
                continue
            k_max = min(n_missing, MAX_ENUMERATED_MISSING)
            missing = [(prev + step * k).to_pydatetime() for k in range(1, k_max + 1)]
            unexpected = [t for t in missing if not period_is_closure(t, secs, hours)]
            closure = len(missing) - len(unexpected)
            base = {"previous_ts_utc": prev.isoformat(), "next_ts_utc": nxt.isoformat(),
                    "missing_candles": n_missing}
            if closure:
                out.append(QualityIssue("market_closure_gap", INFO, prev.to_pydatetime(),
                                        {**base, "closure_candles": closure}))
            if unexpected:
                out.append(QualityIssue("missing_candles", WARNING, unexpected[0],
                                        {**base, "unexpected_candles": len(unexpected),
                                         "first_missing_utc": unexpected[0].isoformat(),
                                         "last_missing_utc": unexpected[-1].isoformat(),
                                         "truncated": n_missing > MAX_ENUMERATED_MISSING}))
        return out

    def _jump_issues(self, df: pd.DataFrame, timeframe: str, hours: TradingHours) -> list[QualityIssue]:
        out: list[QualityIssue] = []
        if len(df) < 12:
            return out
        rng = (df["high"] - df["low"]).astype("float64")
        typical = rng.shift(1).rolling(self.range_window, min_periods=10).median()
        jump = (df["open"] - df["close"].shift(1)).abs()
        flag = (jump > self.gap_range_multiple * typical) & typical.gt(0)
        secs = TIMEFRAME_SECONDS[timeframe]
        for i in np.nonzero(flag.to_numpy())[0]:
            prev_ts = df["ts_utc"].iloc[i - 1]
            spans_closure = (df["ts_utc"].iloc[i] - prev_ts) > pd.Timedelta(seconds=secs) * 1.1
            out.append(QualityIssue("abnormal_gap", WARNING, df["ts_utc"].iloc[i].to_pydatetime(),
                                    {"previous_close": _py(df["close"].iloc[i - 1]), "open": _py(df["open"].iloc[i]),
                                     "jump": _py(jump.iloc[i]), "typical_range": _py(typical.iloc[i]),
                                     "multiple": round(float(jump.iloc[i] / typical.iloc[i]), 2),
                                     "spans_market_closure": bool(spans_closure)}))
        return out


def identical_all(dups: pd.DataFrame) -> bool:
    if dups.empty:
        return True
    return all(len(g[PRICE_COLS].drop_duplicates()) == 1 for _, g in dups.groupby("ts_utc"))


def _ts(df: pd.DataFrame, i: int) -> datetime | None:
    v = df["ts_utc"].iloc[i]
    return None if pd.isna(v) else pd.Timestamp(v).to_pydatetime()


def _py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.generic,)):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v
