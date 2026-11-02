# COT methodology (AURUM AI Module 02)

This document defines every number and label the COT engine produces. Code references are in
`aurum/cot/`. Thresholds marked *configurable* live in `config/cot.yaml` and are **AURUM conventions, not
industry standards**.

## 1. What a COT report is

The U.S. Commodity Futures Trading Commission (CFTC) publishes weekly *Commitments of Traders* reports: the
open futures positions of every market where 20 or more traders hold reportable positions, aggregated by
trader category. Positions are measured **as of Tuesday** (the *report date*) and normally **released the
following Friday at 15:30 US/Eastern** (the *release*). AURUM uses the *futures-only* versions.

* **Long / short** - contracts held long / short at the close of the report date.
* **Spreading** - contracts a trader holds both long and short in the same market (e.g. different months).
  Reported only for some categories (non-commercials; TFF dealer/asset manager/leveraged/other; Disaggregated
  swap dealers/managed money/other). Where a family has no spreading field, AURUM stores `NULL` - never 0.
* **Open interest (OI)** - total contracts outstanding. Identity used by the quality checks (holds in all
  three families): `OI = Σ long (non-total categories) + Σ spreading = Σ short + Σ spreading`.
* **Reportable / non-reportable** - traders at or above the CFTC reporting level vs. the remainder
  (non-reportable = OI minus reportable). Non-reportables are **not an exact measure of retail traders**.

COT data does **not** identify individual institutions and does **not** reveal profits or losses.

## 2. Report families and categories

| Family (`report_type`) | Categories (CFTC definitions, summarised) | AURUM use |
|---|---|---|
| **Legacy** (`legacy`) | *Commercial* - positions used to hedge a business; *Non-commercial* - large speculators; *Non-reportable* | all 4 instruments |
| **Traders in Financial Futures** (`tff`) | *Dealer/Intermediary* - sell-side (banks, dealers); *Asset Manager/Institutional* - pension funds, mutual funds, insurers, endowments; *Leveraged Funds* - hedge funds, CTAs, money managers; *Other Reportables*; *Non-reportable* | EURUSD, GBPUSD, US30 |
| **Disaggregated** (`disaggregated`) | *Producer/Merchant/Processor/User* - physical commodity businesses; *Swap Dealers*; *Managed Money* - CTAs, CPOs, hedge funds; *Other Reportables*; *Non-reportable* | XAUUSD |

Categories are never compared or charted **across** families: the classifications differ (e.g. Legacy
"commercial" includes swap dealers that the Disaggregated report separates).

Headline ("primary") category per family: Legacy → Non-commercial, TFF → Leveraged Funds, Disaggregated →
Managed Money (configurable per mapping).

## 3. Publication timing and look-ahead protection (`aurum/cot/release.py`)

Each stored report has `report_date`, `release_date`, `release_timestamp` (UTC), `release_method`,
`release_uncertain`, `retrieval_timestamp` and `processed_at`.

Release resolution:
1. **Official overrides** from CFTC publications (`release.overrides`): holiday weeks in the 2026 schedule and
   the Oct-Dec 2025 shutdown catch-up (press release 9147-25). Time 15:30 ET (the press release states it
   for the first catch-up report; assumed for the others - documented uncertainty, conservative because the
   whole day is withheld until 15:30).
2. **Rule**: Friday of the report week, 15:30 America/New_York. If a U.S. federal holiday falls Tuesday-Friday of
   that week, the next business day after Friday. Verified: reproduces all 52 release dates of the official
   2026 schedule (including the 6 holiday weeks) without overrides.
3. **Uncertain ranges** (2013 and 2018-19 lapses, no official catch-up table loaded): rule date used, report
   flagged `release_uncertain` + data-quality warning `release_date_uncertain`.

A report is usable at instant *t* only if `release_timestamp <= t`. Every API/CLI view accepts `as_of`
(`--as-of`, `?asOf=`) and filters on the release timestamp, never on the report date.

## 4. Metrics (`aurum/cot/calculations.py`, `positioning.py`)

For one contract mapping, one category, report *t* (reports sorted by report date):

| Metric | Definition | Edge cases |
|---|---|---|
| Net position | `long - short` | `NULL` if either side missing |
| Weekly net change | `net(t) - net(previous stored report)` (same contract, same category) | `NULL` for the first report |
| Long / short change | `long(t) - long(t-1)`, `short(t) - short(t-1)` | used to name the *driver* |
| Net % of OI | `net / OI × 100` | `NULL` if OI missing or ≤ 0 |
| Window *W(L)* | reports with `report_date ∈ (t − L weeks, t]` - current included, **never later reports** | |
| Historical percentile (12/24/36/48 m = 52/104/156/208 weeks) | mid-rank: `(count(x < v) + 0.5·count(x = v)) / n × 100` over *W(L)* | `NULL` when *W(L)* has fewer than `max(min_history_weeks, ⌈0.8·L⌉)` observations |
| COT Index (L = 156 w, configurable) | `(v − min) / (max − min) × 100` over *W(L)* | `NULL` when `max = min` or insufficient history |
| Z-score (L = 156 w, configurable) | `(v − mean) / std`, population std (ddof 0) over *W(L)* | `NULL` when `std = 0` or insufficient history |

Because the CFTC publishes reports in chronological order, every report in *W(L)* was public by *t*'s
release: metrics for *t* are exactly what could have been computed at that time. Recalculation is
deterministic (`calculation_version` is stored with every row); the test
`test_historical_metrics_do_not_change_when_future_reports_arrive` proves later data does not alter earlier
metrics.

## 5. Classification (*configurable*)

`score = mean of the available values among {36-month percentile, COT index}`

| score | label |
|---|---|
| ≥ 90 | extremely bullish |
| ≥ 75 | strong bullish |
| ≥ 60 | moderately bullish |
| 40 < score < 60 | neutral |
| > 25 | moderately bearish |
| > 10 | strong bearish |
| ≤ 10 | extremely bearish |

Direction rule (`require_direction_for_extremes`): *extremely bullish* also requires a net-long position and
*extremely bearish* a net-short one; otherwise the label is capped at *strong*. The label describes
positioning **relative to the group's own history** - it is not a forecast. For commercial / producer /
dealer groups (mainly hedgers) a "bullish" label means their net position is high versus their own history;
hedgers' positions often move opposite to price, so read them as context, not as a directional view.

Instrument-level stance (`overall`): bullish/bearish only when the primary categories of all families agree
strongly (mean score ≤ −2 or ≥ +2 on a −3…+3 scale); moderate agreement → *neutral* with a *lean*; opposite
non-neutral readings across families → *mixed*.

## 6. Positioning changes

* `change_type`: `net_long_accumulation`, `net_long_reduction`, `net_short_accumulation`,
  `net_short_reduction`, `reversal_to_net_long`/`reversal_to_net_short` (sign flip), `new_net_long`/`new_net_short`
  (from exactly zero), `unchanged`.
* `change_driver`: the side that moved most - `longs_added`, `longs_cut`, `shorts_added`, `shorts_covered`.
* `significant_change`: |z| ≥ 2 (*configurable*) of this week's net change against **all earlier** weekly
  changes (needs ≥ `min_history_weeks` of them; constant history → not judged).
* `unusual_positioning`: 36-month percentile ≤ 5 or ≥ 95 (*configurable*).
* Trend: number of consecutive reports in which the net position moved the same way.

## 7. Participant comparisons (within one family only)

TFF: Dealer vs Leveraged Funds, Asset Manager vs Leveraged Funds, Leveraged Funds vs Non-reportable.
Legacy: Commercial vs Non-commercial, Non-commercial vs Non-reportable.
Disaggregated: Managed Money vs Producer/Merchant, Managed Money vs Swap Dealers, Managed Money vs Non-reportable.
Each comparison reports positioning relation (*opposed*, *aligned*, *mixed*), flow relation (this week's
changes in the same or opposite direction) and the 36-month percentile gap.

## 8. Price-position divergence (`aurum/cot/divergence.py`)

* Price source: Module 01 **completed, non-invalid** candles, timeframe D1 (*configurable*).
* Alignment: report date *T* → close of the last D1 candle whose day ≤ *T* (the Tuesday close), at most 4 days
  earlier; otherwise no price. With `as_of`, only candles fully closed by then (`ts + 1 day ≤ as_of`).
* Window: the last `window_reports` = 4 report intervals (*configurable*). Signals:
  * **bearish divergence** - price change ≥ +0.5 % while the group's net position fell;
  * **bullish divergence** - price change ≤ −0.5 % while the net position rose;
  * **unconfirmed high** - price above every aligned close of the previous 26 reports while the net position
    is below its maximum of that window;
  * **unconfirmed low** - price below every previous close while the net position is above its minimum.
* Output always includes the report dates and price dates used and the stored price coverage. Module 01
  currently keeps ~500 D1 candles (~2 years), so divergence history is limited to that period.
* A divergence is a disagreement between price and positioning - **not** evidence of an imminent reversal.

## 9. Confidence

Per family: *high* when none of these apply, *medium* with one, *low* with two or more or when stale:
fewer than 156 weeks of history, freshness not `current`, release date uncertain, open data-quality errors,
mapping not verified. Freshness: `current`, `update_due` (the next scheduled release passed 6 h ago and no
newer report is stored), `stale` (latest release older than `stale_after_days` = 10).

## 10. Limitations

* Weekly, delayed data (positions are ~3 days old when published, ~10 days old before the next release).
* Futures-only: options, OTC and spot positions are not included; the futures contract is a proxy for the
  broker CFD (e.g. CME Euro FX vs EURUSD; E-mini Dow $5 vs US30).
* Category assignment is the CFTC's; a trader's category may not reflect each individual position's purpose.
* Thresholds are conventions; historical percentiles depend on the lookback chosen.
* COT analysis is medium- to long-term context, never a standalone entry signal.
