# Trade Learning Agent

Watches every closed trade, identifies what works, re-tests it on new trades, replicates when it
holds up, and terminates itself after three failed trials in a row.

**Read-only.** The agent reads trade history and writes its own notes. It cannot place, modify or
close trades: the MT5 proxy still blocks every order function. "Repeating the process" means
re-testing a pattern on the trades that close next (forward testing), not trading it.

## The cycle

| Step | What happens | Default rule (`LearningConfig`) |
| --- | --- | --- |
| Monitor | Every closed trade is read and tagged: symbol, side, session (DST-aware), weekday, strategy (MT5 comment or magic number), holding time | `features` |
| Identify what works | In the most recent trades, find the pattern (1-2 conditions) with the strongest positive edge, ranked by the t-statistic of its average result | window `discovery_window=100`, ≥ `min_pattern_trades=20`, profit factor ≥ `min_profit_factor=1.3`, average > 0 |
| Trial | Judge the pattern on its next matching trades that closed **after** it was found | `trial_trades=10`; passes when net > 0 and PF ≥ `trial_min_profit_factor=1.2`; inconclusive after `trial_max_wait=300` closed trades |
| Pass → replicate | The agent retires and creates **one** child (generation + 1) that repeats the whole cycle on the newest trades | stops at `max_generations=20` |
| Fail → retry → terminate | The failed pattern is never re-tried; the agent looks for another. After 3 failed trials in a row it terminates itself, and the lineage ends | `max_failed_trials=3` |

One agent is active per lineage at any time, so replication never fans out.

## Running it

```bash
python -m aurum.cli learn run --loop            # MT5 deal history (demo account), checks every 5 min
python -m aurum.cli learn run --csv trades.csv  # or a CSV export
python -m aurum.cli learn status                # generations, patterns, trials, recent events
python -m aurum.cli learn status --json
python -m aurum.cli learn reset --yes           # delete the lineage; next run starts generation 1
```

CSV columns: `ticket, symbol, side, open_time, close_time, pnl`, with optional `volume, open_price,
close_price, strategy`. Times are ISO 8601; times without a zone are read as UTC.

State lives in `data/learning/L1.json` (written atomically), so the cycle continues across runs. The
frontend's Agent Centre reads it from `GET /api/v1/agents/learning`.

## Reading the results honestly

A pattern that passes one forward trial of 10 trades is a lead, not proof: 10 trades is a small
sample. Treat replication as "keep watching this", and never as a reason to trade bigger. Any future
use of a pattern for trading goes through the AI Signals approval flow, with backend risk checks and
a human decision.
