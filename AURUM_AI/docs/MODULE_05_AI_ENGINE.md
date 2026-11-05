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
3. Runs eight strategy templates. Each gives a candidate or a stated reason it found nothing.
4. Scores evidence deterministically (grades A-F) and runs hard gates.
5. Optionally asks an AI reviewer. It can lower a candidate or reject it, and it can never raise or create one.
6. Saves a final decision and publishes candidates for human review.
7. Labels outcomes from later candles, and replays history for evaluation.

Decisions: `STRONG_BUY_SETUP`, `BUY_SETUP`, `STRONG_SELL_SETUP`, `SELL_SETUP`, `WAIT`, `NO_TRADE`, `INSUFFICIENT_DATA`.
A "setup" decision is a research label for review. It is not an instruction to trade.

Confidence measures how far the evidence agrees. It is not a probability of success.

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
