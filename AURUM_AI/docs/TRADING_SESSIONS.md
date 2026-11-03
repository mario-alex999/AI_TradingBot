# Trading sessions, kill zones, overlaps, levels and session liquidity

## Session model
Every window is a **wall-clock** interval in an IANA time zone (`config/market_intelligence.yaml`). DST is applied by the tz database (`zoneinfo` + `tzdata`); no UTC offsets are hard-coded.

| Session | Time zone | Local hours | UTC in Jan | UTC in Jul |
|---|---|---|---|---|
| sydney | Australia/Sydney | 07:00–16:00 | 20:00–05:00 (AEDT, previous UTC day) | 21:00–06:00 (AEST) |
| tokyo | Asia/Tokyo | 09:00–18:00 | 00:00–09:00 | 00:00–09:00 (no DST) |
| asian | Asia/Tokyo | 07:00–16:00 | 22:00–07:00 | 22:00–07:00 |
| london | Europe/London | 08:00–17:00 (pre-session 60 min) | 08:00–17:00 (GMT) | 07:00–16:00 (BST) |
| new_york | America/New_York | 08:00–17:00 (pre-session 30 min) | 13:00–22:00 (EST) | 12:00–21:00 (EDT) |

### Rules
- **Weekdays** are the *local* day on which a window starts. A close earlier than the open means the window crosses local midnight; for example, `asian_kz` runs 20:00–00:00 New York.
- **Holidays** are local dates on which the window does not open, configured per session. Examples: 2026-11-26 (US Thanksgiving) and 2026-12-25.
- **Instrument trading hours.** `window_status(..., hours)` combines the session clock with each instrument's Module 01 trading hours. A session that is "open" by the clock while the instrument's market is closed reports `market_closed` and `active=false`. For example, Sydney's Monday session starts Sunday 20:00 UTC under AEDT, an hour before FX reopens.
- **Status values:**
  - `open`;
  - `pre_session` (within `pre_minutes` of the open);
  - `post_session`;
  - `closed`;
  - `market_closed`.
- **DST gap weeks** are handled naturally. Between the US change (2026-03-08) and the UK change (2026-03-29), London–New York overlaps for five hours (12:00–17:00 UTC) instead of four.

## Kill zones
Kill zones are configurable windows, named and treated exactly like sessions:

| Kill zone | Zone | Hours |
|---|---|---|
| `asian_kz` | New York | 20:00–00:00, Sun–Thu |
| `london_kz` | London | 07:00–10:00 |
| `new_york_kz` | New York | 07:00–10:00 |
| `london_close_kz` | London | 15:00–17:00 |

`instrument_kill_zones` can replace the list per instrument. By default, US30 uses only `new_york_kz` and `london_close_kz`.

## Overlaps and transitions

| Overlap | How it is defined | Example (Jan, UTC) |
|---|---|---|
| `london_new_york` | intersection of London and New York | 13:00–17:00 |
| `sydney_tokyo` | intersection of Sydney and Tokyo | — |
| `asian_london` | the sessions do not intersect, so a transition window of ±`transition_minutes` around the hand-over is used, marked `kind: transition` | 06:30–08:30 |

Overlap activity is not assumed. `statistics` measures it from candles, as the mean true range inside the overlap divided by the mean true range of all candles.

## Levels (`levels.py`)

| Level | Period |
|---|---|
| previous / current Asian, London, New York high & low | latest completed occurrence / the open occurrence |
| PDH / PDL, current day | FX trading day, rolling at 17:00 America/New_York |
| PWH / PWL, current week | Sunday 17:00 → Friday 17:00 New York |
| PMH / PML, current month | UTC calendar month |

### Method
- The finest stored timeframe that covers the period is used (M5 → M15 → H1).
- If intraday history does not reach back far enough, D1 is used:
  - for days and weeks, with a note that the boundary becomes UTC midnight;
  - for months, D1 is exact, so no note is added.
- Only candles with `close_t <= as_of` are used. The current period's levels are marked `provisional`.
- Each level records:
  - the source candle time;
  - the timeframe;
  - the candle count, expected count and missing count;
  - `formed_at`.
- **State:**
  - `untouched`;
  - `swept`: a wick traded beyond the level but the candle closed back inside;
  - `broken`: a candle closed beyond the level.

  Both are evaluated only after `formed_at`.

## Session liquidity (`liquidity.py`)

| Pair | Meaning |
|---|---|
| `london` vs `asian` | does London take the Asian high or low? |
| `new_york` vs `london` | the London range is cut at the New York open, so the levels are the *same-day* London range before New York, not yesterday's |
| `new_york` vs `asian` | |

PDH, PDL, PWH and PWL interactions and session range expansion are also reported.

### Classification
Defaults: `confirm_bars=2`, `false_breakout_bars=3`, so `need = max(2, 3) = 3`.
- **`wick_sweep`:** a wick trades beyond the level, and the candle closes back inside.
- **`false_breakout_candidate`:** a candle closes beyond the level, then one of the next `false_breakout_bars` candles closes back inside.
- **`confirmed_breakout`:** a candle closes beyond the level, and each of the next `need` candles also closes beyond it.
- **`unconfirmed`:** a candle closes beyond the level, but fewer than `need` candles have closed since. The verdict is not yet decidable at `as_of`.
- **`range_expansion`:** the session range is at least `expansion_ratio` (1.5) × the mean of the previous `expansion_lookback` (10) same sessions.

Each event lists the rule applied, its timeframe and its confirmation time. Optionally, it also lists `related_structure_event`, which is a Module 03 BOS/CHoCH on M15 (H1 fallback) within `module03_match_minutes` (120) after the interaction.

Events are descriptions of what happened. Their `interpretation` field says they are not signals.

## Session statistics (`statistics.py`)
Statistics are computed per session and overlap, over windows of the last 5, 10, 20, 30 and 60 completed sessions (H1 candles):
- mean and median range;
- range as a percentage of price;
- net movement;
- realised volatility percentage;
- session ATR;
- mean tick volume;
- activity ratio: elevated at ≥ 1.2 and subdued at ≤ 0.8.

Each window reports `sample_size`, `sufficient` (≥ the window size) and `usable` (≥ `min_sample` = 3). Missing sessions are listed. Breakout frequency is reported per pair; daily and weekly range statistics come from D1.
