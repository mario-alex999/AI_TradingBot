# Volatility engine

Volatility is calculated from stored Module 01 candles, using only those closed at `as_of`. It is computed for H1 (the primary regime timeframe) and D1.

| Measure | Definition |
|---|---|
| ATR | Wilder ATR(14), reusing Module 03's `wilder_atr` / `prepare_candles` (no duplicate implementation) |
| ATR % of price | ATR / last close × 100 |
| ATR percentile | rank of the current ATR among the previous `percentile_lookback` (250) ATR values, **causal** (past values only) |
| Returns std % | sample standard deviation of the last `returns_window` (20) log returns × 100 |
| Realised vol % | √(Σ r²) over the same 20 log returns × 100 (window realised volatility) |
| Annualised vol % | returns std × √(bars per year) × 100 (252 trading days × bars per day) |
| Realised-vol percentile | causal percentile of the rolling 20-bar realised volatility |
| Expansion state | mean true range of the last 5 bars / last 20 bars: ≥ 1.25 `expansion`, ≤ 0.8 `contraction`, else `neutral` |
| Abnormal movement | the last bar's range ≥ 2.5 ATR, or \|last return / std of the previous 20\| ≥ 3 |
| Daily / weekly ratio | current day/week range vs the 20-day / 10-week average (provisional while the period is open) |
| Session ratios | latest completed session range vs the mean of the last 20 same sessions |

## Regimes
| ATR percentile | Regime |
|---|---|
| < 10 | VERY_LOW |
| 10–30 | LOW |
| 30–70 | NORMAL |
| 70–90 | HIGH |
| ≥ 90 | VERY_HIGH |
| fewer than `min_bars` (60) closed candles | UNKNOWN |

The cut-offs are configurable (`volatility.regimes`). Each regime carries a `reason`, for example: "Current ATR is at the 89th percentile of its recent history (cut-offs 10/30/70/90)."

The response shape is stable: with insufficient data, every metric key is present with the value `null`.

`event_window_range` measures the range in the `history_window_minutes` (60) after a past release, relative to ATR. It feeds the event-risk score's "historical sensitivity" component.

**Interpretation.** Regimes describe the present relative to the instrument's own history. They are not volatility forecasts.
