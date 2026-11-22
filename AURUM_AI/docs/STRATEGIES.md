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
plus `levels.stop_buffer_atr` and the spread) is widened to at least that many ATR of the entry timeframe, the
candidate's sources say so, and the target must still give `gates.min_rr` from the wider stop. It was added on 22 Nov
2026 after the live outcomes showed 0 take profits against 12 stop losses with 2–2.5-pip stops. The backtest of 10–20 Nov
2026 (4 markets, the engine's own replay) did not support it, so the default is 0:

| Rules | Trades | Win rate | Net R | Profit factor |
|---|---|---|---|---|
| Before 22 Nov | 117 | 34% | +57.9R | 1.67 |
| Selection rules only (below) | 76 | 37% | +44.6R | 1.84 |
| 1 ATR minimum stop only | 112 | 34% | +20.6R | 1.25 |
| Both | 74 | 36% | +19.0R | 1.37 |

Wider stops shrank each win (the targets stay at the same liquidity) more than they raised the win rate. Ten days is a
small sample; retest on more history before switching the minimum on.

**Which candidates become signals** (`aurum/decision/selection.py`, the same in live runs and the backtest):

* *One signal per idea.* Candidates in the same direction whose entry zones overlap, within `selection.overlap_atr` ×
  ATR (default 0.5), are one idea, wherever they come from (several templates on one zone, M15 and H1). The strongest is
  kept and the others are dropped with a reason in the run's `skipped` list. A later run does not republish an idea that
  a live signal (new or under review) already covers. Dropped candidates are not stored.
* *Counter-trend reversals need a strong grade.* `choch_reversal`, `liquidity_sweep_reversal` and
  `range_edge_rejection` trading against every higher timeframe (D1 and H4) are kept only at the grades in
  `selection.counter_trend_min_grades` (default A, B).

"Needs alignment" means a `WAIT` decision when timeframes disagree or the bias opposes the trade.
