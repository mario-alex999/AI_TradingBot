# Module 05 strategies

Every strategy is a deterministic function of closed candles, in `aurum/decision/candidates/setups.py`. None is proven
profitable. Each is switched in `config/ai_engine.yaml`, and `ai strategies list` shows the current state.

| Requested strategy | Template | Needs alignment | Enters on |
|---|---|---|---|
| Liquidity Sweep Reversal | `liquidity_sweep_reversal` | No | Rejection after a sweep of a pool |
| Order Block Retest | `order_block_continuation` | Yes | A retest of an unmitigated block after a break |
| Fair Value Gap Retracement | `fair_value_gap_retracement` | Yes | A pullback into an open gap |
| BOS Continuation | `bos_continuation` | Yes | A pullback after a recent break of structure |
| CHoCH Reversal | `choch_reversal` | No | A confirmed change of character |
| Session-Based | `session_breakout` | No | M15 break of the Asian range in London or New York |
| Premium/Discount + OTE | `premium_discount_ote` | Yes | Price in the OTE zone on the right side of equilibrium |
| (kept from the first specification) | `range_edge_rejection` | No | Rejection at a strong range edge |

Each gives direction, entry zone, stop, one or two targets, expiry, an invalidation sentence and its sources. Stops and
targets come from `candidates/levels.py`, using structure and ATR. They are never model output.

**Minimum stop distance (off by default).** With `levels.min_stop_atr` above 0, the structure stop (beyond the zone,
plus `levels.stop_buffer_atr` and the spread) is widened to at least that many ATR of the entry timeframe, and the target
must still give `gates.min_rr` from the wider stop. Off (0), because the benchmark below rejected it. Why it hurt,
matching the same 83 signals traded under both rules:

* Winners shrank: the targets stay at the same liquidity, so the average win fell from +4.55R to +2.61R.
* Losers were not saved: 47 of 52 losing trades still lost with a stop twice as far away. Price went through the level;
  it was not noise.
* It switched off a filter: "stop too close for the spread" had blocked 118 weak setups; with wider stops only 15 were
  blocked, and the extra trades lost −5.6R.

**Which candidates become signals** (`aurum/decision/selection.py`):

* *Counter-trend reversals need a strong grade* (before the gates). `choch_reversal`, `liquidity_sweep_reversal` and
  `range_edge_rejection` trading against every higher timeframe (D1 and H4) are kept only at the grades in
  `selection.counter_trend_min_grades` (default A, B).
* *One signal per idea* (after the gates, `one_per_idea`). Candidates in the same direction whose entry zones overlap,
  within `selection.overlap_atr` × ATR (default 0.5), are one idea; the strongest one that passed the gates is kept and
  the others are dropped with a reason. A later run does not republish an idea that a live signal already covers. The
  first version chose before the gates and lost the whole idea when the chosen copy then failed a gate.

**Benchmark, 10–20 Nov 2026** (`decide benchmark`, 4 markets, stored candles; see
[HISTORICAL_EVALUATION.md](HISTORICAL_EVALUATION.md)):

| Profile | Trades | Win rate | Net R base / high / stress costs | Profit factor | Worst drawdown | Beats baseline |
|---|---|---|---|---|---|---|
| baseline (frozen) | 117 | 34% | +57.9 / +40.3 / +16.9 | 1.67 | 8.3R | — |
| **current** | 81 | 43% | **+77.4 / +65.3 / +49.1** | **2.51** | **5.6R** | **yes** |
| no_choch | 73 | 45% | +64.6 / +54.3 / +40.3 | 2.49 | 4.5R | yes |
| min_stop_1atr | 112 | 34% | +20.6 / +5.0 / −16.2 | 1.25 | 8.3R | no |
| choch_only (diagnostic) | 74 | 20% | −8.7 / −20.6 / −36.2 | 0.87 | 25.9R | — |

What it shows: trades against both D1 and H4 lost −22.1R in the baseline (39 trades, 13% won); with the trend they made
+65.5R (72 trades, 44% won). CHoCH lost only against the trend (−24.9R on 36 trades); with the trend it made +15.2R, so it
stays enabled. Recommended A/B setups made +29.5R on 14 trades (71% won) in both baseline and current. The New York
session lost in every profile (about −10R on 10–12 trades), and the period was mostly a bearish trend.

Ten days, one market phase, about 80–120 trades: these are leads, not proof. A high profit factor on few trades is not
an edge, and backtests that look good can fail live. Retest on months of history (see *More history* in
HISTORICAL_EVALUATION.md) before trusting or extending any of it.

