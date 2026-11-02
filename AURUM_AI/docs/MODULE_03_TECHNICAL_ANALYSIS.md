# AURUM AI - Module 03: Technical Analysis and SMC Engine

> **Analysis only.** Module 03 never connects to MetaTrader 5, never places, modifies or closes trades and
> never approves execution. It turns stored candles into deterministic, explainable price-action findings for
> a human operator. "Displacement", "order block" and "liquidity" are price-action classifications, not proof
> of who placed orders.

## 1. Architecture assessment (done before implementation)

| Area | What existed | How Module 03 uses it |
|---|---|---|
| Project | One Python package `AURUM_AI/aurum/`: Module 01 (`gateways/`, `collectors/`, `services/`, `database/`, `exporters/`), Module 02 (`cot/`), FastAPI (`api/`); React app in `aurum-frontend/` | New package `aurum/analysis/`; nothing in Module 01/02 is rewritten |
| Database | SQLAlchemy models on one shared `Base`; PostgreSQL via `sql/schema.sql` + numbered migrations (`0002_cot_engine.sql`); SQLite via `create_all` | Migration `0003_technical_analysis.sql` (+ ORM in `analysis/services/db_models.py`, registered with one import in `init_database`, as Module 02 does) |
| Market data | `Repository.load_candles(symbol, timeframe, include_invalid, include_incomplete)` (UTC candles with `is_complete`, `quality_status`); `ValidationService` (OHLC rules, duplicates, ordering, closure-aware gaps) | The only candle source. Module 01's validator is reused, so both modules apply identical rules. **No second MT5 connection.** |
| API | FastAPI factory `create_app`, `/api/v1`, camelCase via `camelize()`, admin guard `X-AURUM-Admin-Key` | New router `/api/v1/analysis/*` with the same conventions |
| Module 02 | Present and merged; `aurum.cot.intelligence.instrument_report()` returns an overall stance / lean / confidence | Optional COT confluence through a read-only adapter |
| Analysis code | None in the backend. The frontend already called `GET /analysis/market-structure` (unanswered) | Implemented with the exact frontend contract (`MarketStructureReport`, `ChartOverlay[]`) |

Compatibility notes: Module 01 tables, functions and behaviour are unchanged (one additive import line in
`init_database`). The development prompt for this module was cut off inside section 13 (BPR); the remaining
items from its section 3 list (liquidity, inducement, premium/discount, Fibonacci, OTE, S/R, multi-timeframe,
quality validation, scoring) were implemented with the standard definitions documented below.

## 2. Layout

```
aurum/analysis/
  config.py                    every threshold; defaults + config/analysis.yaml; config fingerprint
  data.py                      candle preparation (reuses Module 01 ValidationService), causal ATR
  technical_engine.py          single-timeframe pipeline
  market_structure/            swing_detection · displacement · bos_choch · structure_engine
  smart_money/                 order_blocks · fair_value_gaps · balanced_price_ranges · liquidity ·
                               inducement · premium_discount · ote
  support_resistance/          zone_detection · zone_scoring
  multi_timeframe/             timeframe_analysis (as-of alignment) · confluence_engine
  scoring/                     technical_scoring · quality_filters
  models/                      candle · swing · structure_event · order_block · fair_value_gap ·
                               liquidity_pool · support_resistance · analysis_result
  services/                    analysis_service · analysis_repository · analysis_cache · cot_adapter ·
                               db_models · frontend_views
  cli.py                       python -m aurum.cli analyze ...
aurum/api/routes/technical_analysis.py
config/analysis.yaml   sql/migrations/0003_technical_analysis.sql   tests/analysis/
```

## 3. Data input and the non-repainting policy

* Candles: symbol, timeframe, UTC open time, OHLC, tick/real volume, `is_complete`, source.
* Validation (Module 01 rules): high ≥ open/close/low, low ≤ open/close/high, numeric and positive prices,
  duplicates (first kept), chronological order, gaps split into market closures vs unexpected gaps.
  Rows Module 01 marks `invalid` are **excluded and listed** in `data_quality.excluded`; nothing invalid
  reaches a detector. Naive timestamps are interpreted as UTC and reported.
* Closed candles only by default. `includeProvisional=true` keeps the last forming candle; everything it
  confirms is flagged (`provisional`, `confirmed: false`) and the result carries a warning.
* **Every finding has a source time and a confirmation time.** A swing exists from the close of candle
  `i + right`; a BOS/CHoCH from the close of the breakout candle; an order block from the close of the
  breakout candle that qualified it; an FVG from the close of candle 3; a previous-day pool from the close of
  the day's last candle. State changes (mitigation, fills, sweeps) only use candles after detection.
* Multi-timeframe: each timeframe only sees candles that had **closed at `asOf`** (a D1 candle opened today is
  invisible to an M15 analysis at 10:00).
* Verified by `tests/analysis/test_no_lookahead.py`: analysing candles `0..k-1` reports exactly the swings,
  structure events, order blocks, FVGs and liquidity pools the full-history analysis reports as known by `k-1`.

## 4. Definitions

All sizes are ATR multiples (Wilder ATR, causal), so one configuration fits EURUSD, XAUUSD and US30.

**Swings.** High at `i`: `high[i] >` every high in the left window and `>=` every high in the right window
(lows mirrored). In a run of equal highs only the first candle qualifies. Internal (default 2/2) and external
(5/5) windows. Recorded: type, price, source time, confirmation time, strength (left dominance + right window),
prominence (ATR). Same-type swings closer than `min_separation_bars` keep the earlier one (never repaint).

**Structure.** Each swing is labelled against the previous swing of its kind: HH/LH, HL/LL, or EQH/EQL within
`equal_tolerance_atr`. Bias comes from confirmed breaks (below); the HH/HL (LL/LH) sequence raises or lowers
confidence. States: `trending`, `transitioning` (latest event is a CHoCH, or the sequence disagrees),
`ranging` (no qualifying break yet - never forced), `insufficient_data` (fewer than two swing highs and lows;
any break-implied bias is kept with confidence capped at 30%).

**BOS / CHoCH.** The relevant levels are the most recent confirmed swing high and low of the scope. A candle
**close** beyond the level plus `max(break_buffer_atr × ATR, min_break_price)` confirms a break (wick mode is
optional). In the bias direction = BOS; against an established bias = CHoCH; the first break without a bias is
an *initial* BOS. Each event records broken level, broken swing time, breakout candle time, confirmation time,
previous/new bias, internal/external, displacement strength. CHoCH status: **confirmed** (latest event is a
CHoCH on a closed candle), **potential** (the last candle traded through the protecting level without closing
beyond it, the breakout candle is still forming, or - for external structure - internal structure already
turned against it), **none**.

**Displacement.** Single candle: body ≥ 1.0 ATR, range ≥ 1.2 ATR, body/range ≥ 0.6, close in the outer 30%;
or a run of 3 same-direction candles whose combined body ≥ 1.5 ATR. Volume adds 0.25 to strength only when
the feed's volume actually varies. Linked to the break it caused.

**Order blocks.** Bullish: the last bearish candle before the bullish displacement that produced a qualifying
BOS/CHoCH (bearish mirrored). Requires a structure break and displacement by default. Zone: full wick, body, or
open-to-extreme. States: `fresh` → `partially_mitigated` → `mitigated` (first touch / 50% / full penetration,
configurable), `invalidated` (close beyond the distal edge), `expired` (after `expiry_bars`). `detected_at` is
never earlier than the breakout candle's close.

**Fair value gaps.** Bullish: `low(c3) > high(c1)`, gap `[high(c1), low(c3)]`; bearish: `high(c3) < low(c1)`, gap
`[high(c3), low(c1)]`. Minimum size in ATR and price. States: `unfilled`, `partially_filled`, `fully_filled`,
`invalidated` (close beyond the far edge, configurable), `expired`. Middle-candle displacement is recorded.

**Balanced price range.** A bullish and a bearish FVG detected within 20 bars whose ranges overlap by ≥ 25% of
the smaller gap; the overlap is the BPR, direction of the later gap, invalidated by a close through it.

**Liquidity.** Pools: equal highs/lows (≥ 2 internal swings within 0.1 ATR, price not having traded through
between them), external swing highs/lows, previous UTC day high/low. Buy-side above highs, sell-side below
lows. **Sweep** = wick beyond the level with a close back inside (implication against the sweep); **break** =
close beyond it. A pool is consumed by its first sweep or break.

**Inducement candidates.** Internal pullback swings formed after the latest external break, with any
unmitigated OB/FVG in the break direction beyond them. Reported as candidates with `pending`/`swept` state.

**Premium / discount, Fibonacci, OTE.** Dealing range = latest confirmed external swing high and low;
equilibrium = midpoint; ±5% around it = equilibrium band; above/below the range reported as such. Fibonacci
levels are measured from the leg's end (0) to its start (1). OTE = 62–79% retracement, sweet spot 70.5%, with
overlapping OBs/FVGs listed and a note when it runs against the external bias.

**Support / resistance.** Clusters of confirmed swing prices within 0.5 ATR (≥ 2 members), padded by 0.1 ATR.
Role: support, resistance or flip (both). Score 0–100 = touches (40) + recency half-life (30) + swing strength
(20) + role flip (10), with the breakdown returned.

**Multi-timeframe confluence.** Timeframe weights D1 5, H4 4, H1 3, M15 2, M5 1. Consensus = side holding ≥ 60%
of weight on external structure, else `mixed`. Combined score = weight-averaged timeframe scores + HTF alignment
(entry timeframe internal structure agrees with consensus) + optional COT; penalties for conflict and missing
timeframes.

**Scoring.** Components award points to one side with a sentence each: external structure (20), internal (10),
recent break with/without displacement (10), price inside an active OB (12) or FVG (8), premium/discount (10),
OTE (8), recent liquidity sweep (10), support/resistance (6), COT when available (5). Side score = points ÷
points available × 100. Bias needs a 10-point lead (else `neutral`). Grades A ≥ 75, B ≥ 60, C ≥ 45. Quality
filters subtract: limited history, provisional candle, excluded candles, unexpected gaps; warnings for
internal/external disagreement. **A score measures how much evidence lines up; it is not a probability and not a
trade decision.**

## 5. COT confluence (Module 02)

`services/cot_adapter.py` reads Module 02's stored reports through `instrument_report()` - read-only (no sync,
no fetch). If Module 02 is absent, its tables are empty or the symbol has no mapping, the result says
`available: false` with the reason, and analysis continues on price alone. COT data is never estimated.

## 6. Configuration

`config/analysis.yaml` overrides any default in `aurum/analysis/config.py` (unknown keys are rejected).
Every result stores `engine_version` and a `config_fingerprint`.

## 7. Usage

```powershell
python -m aurum.cli init-db                                   # applies migration 0003 (idempotent)
python -m aurum.cli analyze run --symbol EURUSD --timeframe H1
python -m aurum.cli analyze run --symbol XAUUSD --timeframe M15 --as-of 2026-11-02T10:00 --persist
python -m aurum.cli analyze confluence --symbol EURUSD --timeframes D1,H4,H1,M15
python -m aurum.cli analyze config
python -m aurum.api                                           # API on http://127.0.0.1:8000/docs
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/analysis/technical/{symbol}?timeframe=H1&asOf=&includeProvisional=&includeCot=&detail=summary\|full` | single-timeframe analysis |
| GET | `/api/v1/analysis/confluence/{symbol}?timeframes=D1,H4,H1,M15,M5&asOf=` | multi-timeframe report |
| GET | `/api/v1/analysis/market-structure?instrument=` | frontend `MarketStructureReport` |
| GET | `/api/v1/analysis/market-structure?instrument=&timeframe=&view=overlays` | frontend `ChartOverlay[]` |
| GET | `/api/v1/analysis/config` | effective configuration + fingerprint |
| GET | `/api/v1/analysis/runs`, `/api/v1/analysis/zones` | persisted history |
| POST | `/api/v1/analysis/run` `{symbol, timeframes?, asOf?}` | **admin**: analyse all timeframes and persist |

Errors: unknown instrument 404, bad timeframe 422, database unavailable 503, too few candles → 200 with
`status: insufficient_data`.

## 8. Database (migration `0003_technical_analysis.sql`, additive)

| Table | Content |
|---|---|
| `ta_analysis_runs` | one row per analysis: symbol, timeframe(s), as-of, engine version, config fingerprint, provisional flag, candles used, bias, score, grade, summary JSON |
| `ta_structure_events` | BOS/CHoCH, unique per (symbol, timeframe, scope, type, direction, breakout candle) |
| `ta_zones` | order blocks, FVGs, BPRs, liquidity pools, S/R zones; unique per (symbol, timeframe, kind, zone id); re-runs update state |

## 9. Tests

`tests/analysis/` (no MT5, no network): swings, structure, BOS/CHoCH, order blocks, FVGs, liquidity,
premium/discount, OTE, S/R, scoring, multi-timeframe + service + persistence + API on SQLite, read-only guard,
and the no-look-ahead replay. Run `python -m pytest tests/analysis`; with `AURUM_TEST_DATABASE_URL` set, the
Module 01 database tests also run on PostgreSQL.

## 10. Limitations

* Deterministic rules, not market truth: different window sizes give different swings; tune
  `config/analysis.yaml` per instrument behaviour, then compare results, never in-sample alone.
* Equal-highs/lows and inducement are heuristic candidates.
* CFD/FX tick volume often carries little information; volume only nudges displacement strength when it varies.
* Ambiguous DST hours and broker clock settings are Module 01's responsibility (`MT5_SERVER_TIMEZONE`).
* Scores are evidence summaries for a human reviewer, not probabilities, signals or approvals.
