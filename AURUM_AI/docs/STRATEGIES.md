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

"Needs alignment" means a `WAIT` decision when timeframes disagree or the bias opposes the trade.
