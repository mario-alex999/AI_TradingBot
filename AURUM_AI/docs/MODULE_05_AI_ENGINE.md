# Module 05 - AI Analysis and Decision Engine

Module 05 turns the stored outputs of Modules 01-04 into explained trade candidates for a person to approve or reject.
**It cannot place, change or close a trade.** Approval writes a record and nothing else. Use it on DEMO accounts only.

## Assessment of the existing architecture

Modules 01-04 already expose what this module needs through public services, so it adds an adapter layer and does not
change them. Module 03 and Module 04 are point-in-time (`as_of`), Module 02 honours CFTC publication dates, and
`ReadOnlyMT5` keeps the gateway read-only. The account mirror and Learning Agent are read when they have data and
reported as missing when they do not.

## What it does

1. Builds one `DecisionContext` per symbol from Modules 01-04 as of a given time.
2. Assesses the regime and the weighted bias across D1, H4, H1, M15 and M5.
3. Runs eight strategy templates. Each gives a candidate or a stated reason it found nothing. Stops are at least 1 ATR
   from the entry, one candidate is kept per idea, and weak counter-trend reversals are dropped (see
   [STRATEGIES](STRATEGIES.md)). Stops and targets are
   placed on the instrument's price grid (multiples of its tick size), because the broker accepts no other price and
   Module 06 blocks any other price (R013). A stop is rounded away from the entry, so it stays beyond the level it
   protects. A target is rounded toward the entry, so the reward is never overstated. Risk-reward is calculated from
   the rounded prices.
4. Scores evidence deterministically (grades A-F) and runs hard gates.
5. Optionally asks an AI reviewer. It can lower a candidate or reject it, and it can never raise or create one.
6. Saves a final decision and publishes candidates for human review.
7. Labels outcomes from later candles, and replays history for evaluation. See *Signal outcomes* below.

Decisions: `STRONG_BUY_SETUP`, `BUY_SETUP`, `STRONG_SELL_SETUP`, `SELL_SETUP`, `WAIT`, `NO_TRADE`, `INSUFFICIENT_DATA`.
A "setup" decision is a research label for review. It is not an instruction to trade.

Confidence measures how far the evidence agrees. It is not a probability of success.

## Signal outcomes

After a signal is published, its outcome is labelled from stored M5 candles (`aurum/decision/outcomes.py`). The entry is
a limit at the middle of the entry zone, the price the risk-reward is quoted from. The hosted Module 08 worker runs the
labelling every 5 minutes (step `signal_outcomes`); elsewhere run `python -m aurum.cli decide outcomes-update`.

| State | Meaning |
|---|---|
| `pending` | Not checked yet |
| `waiting` | Price has not reached the entry |
| `running` | Price reached the entry (triggered); neither target nor stop hit yet |
| `take_profit` | Triggered, then the target was hit |
| `stop_loss` | Triggered, then the stop was hit |
| `ambiguous` | Target and stop in the same candle, or the target in the fill candle: counted as a loss (−1R) |
| `closed_at_limit` | Triggered; neither level hit within `outcomes.max_hold_candles` (96 M5 candles); closed at that candle's close |
| `not_triggered` | Expired before price reached the entry |

Each outcome stores when the entry was reached (`filled_at`), the exit price and time, the result in R, and the best and
worst move against the entry (in R). `GET /analysis/signals` and the dossier return it as `outcome`, with `entryPrice`.
The AI Signals page shows it on every card, sums up the results with a win rate (by default only recommended setups:
grade A or B, not rejected by a gate or the AI reviewer; each idea counted once; a switch shows all candidates), filters by result, and draws the trigger
and the exit on the chart, where the position boxes end at the exit. Every outcome is **hypothetical**: AURUM places no
trades, and labels are labels, not fills.

## How two specifications were combined

| Topic | Choice |
|---|---|
| Package | `aurum/decision/`, because the safety scan and the first specification both name it |
| Config | `config/ai_engine.yaml` |
| API | `/analysis/signals`, `/decision/*`, `/backtests/*` and `/ai/*` are thin layers over one `DecisionService` |
| CLI | `decide` for the workflow, `ai` for analysis. Both call the same service |
| Status | Stored: new, under_review, approved, rejected, expired. `reviewStatus` maps them to GENERATED, PENDING_REVIEW, APPROVED_FOR_FURTHER_PROCESSING, REJECTED, EXPIRED, INVALIDATED |
| Tables | Nine tables, not twenty: see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) |
| AI provider | Claude by default, with OpenAI and local servers off until chosen. The rule-based explanation always works and never counts as a review |
| Retries | Default 0, because a timed-out call may already have been billed |

See also: [ARCHITECTURE](ARCHITECTURE.md), [STRATEGIES](STRATEGIES.md), [EVIDENCE_SCORING](EVIDENCE_SCORING.md),
[DECISION_LOGIC](DECISION_LOGIC.md), [HISTORICAL_EVALUATION](HISTORICAL_EVALUATION.md), [SECURITY](SECURITY.md),
[LIMITATIONS](LIMITATIONS.md).
