# News Impact (Module 04, method `news_impact_v1`)

`GET /api/v1/intelligence/news-impact?hoursBack=24&hoursAhead=168&importance=high&currency=USD` returns high-impact
calendar events and headlines with their likely market effect. The frontend shows it on **Intelligence → News Impact**.

## What each event carries

| Field | Meaning |
| --- | --- |
| `expectation` | The forecast compared with the previous figure. |
| `polarity`, `rule` | The indicator's rule: `1` = a higher figure supports the currency (CPI, payrolls, GDP, PMI, rate decisions...), `-1` = it weighs on it (unemployment rate, jobless claims), `null` = no numeric rule (speeches, minutes, press conferences). |
| `scenarios` | For an above-forecast, in-line and below-forecast figure: the currency effect and the direction of each affected instrument. A pair moves with its base currency and against its quote currency (XAUUSD falls when USD strengthens). Indices are `mixed`: strong data can lift them through growth or weigh on them through rates. |
| `scenarios[].instruments[].history` | **Measured** from stored M5 (or M15) candles: how the instrument moved in the 60 minutes after past releases of the same indicator (same currency and subcategory, up to 3 years and 36 releases): number of releases, average move, share of moves up, and share that matched the rule. `null` when no candles cover past releases — never estimated. |
| `outcome` | Once released: the surprise (actual − forecast), the scenario that happened, the currency effect, and per instrument the expected direction, the move measured so far (up to 60 minutes) and whether it followed the rule. |

Headlines: central-bank items and strongly worded market headlines with the existing rule-based tone (`lexicon_v1`).
A hawkish central-bank headline is read as bullish for its currency and a dovish one as bearish. That reading is
mapped only to the instruments the currency drives, using the same impact table as the calendar.

## Limits

These are conditional scenarios and measured history, not a forecast. The actual figure is unknown until it is
released, and markets can react against the conventional rule (positioning, revisions, details of the release,
other news at the same time). Measured history needs stored candles around past releases, so it builds up as Module 01
collects data.
