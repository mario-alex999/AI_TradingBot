# Module 05 database

Migration `sql/migrations/0007_decision_engine.sql` adds nine tables and alters nothing else. `init-db` applies it.
SQLite uses the matching models in `aurum/decision/db_models.py`.

| Table | Holds |
|---|---|
| `decision_runs` | Each run: input hash, config fingerprint, availability, and a JSON summary |
| `trade_candidates` | Candidates, levels, score, status, idempotent key |
| `candidate_evidence` | One row per scoring component |
| `candidate_gate_results` | One row per gate |
| `candidate_ai_reviews` | Audit of every AI attempt, scrubbed of secrets |
| `candidate_status_history` | Who changed what, when, and why |
| `candidate_outcomes` | Hypothetical outcome per candidate |
| `backtest_runs`, `backtest_trades` | Evaluation runs and simulated trades |

## How the twenty suggested tables map

The master prompt suggested about twenty tables. These concepts share the nine above, because they have one writer and
are only read together:

| Suggested | Stored in |
|---|---|
| Analysis runs, snapshots, input hashes | `decision_runs` |
| Regime, bias and timeframe assessments, conflicts, decisions, explanations | `decision_runs.summary` |
| Setups, trade ideas, position size estimates | `trade_candidates` |
| Evidence items and scores | `candidate_evidence`, `trade_candidates` |
| Risk checks | `candidate_gate_results` |
| Provider calls and AI outputs | `candidate_ai_reviews`, and the budget ledger from the AI review migration |
| Review actions | `candidate_status_history` |
| Evaluation runs, trades, metrics | `backtest_runs`, `backtest_trades` |
| Config versions | `config_fingerprint` on each run, with overrides in a file |
