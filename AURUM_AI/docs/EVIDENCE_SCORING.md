# Module 05 evidence scoring

`aurum/decision/scoring.py` adds up components that each carry points, a maximum and one sentence. The weights add up
to 100 and are set in `score.weights`.

| Component | Default weight | Reads |
|---|---|---|
| `setup_quality` | 30 | The zone or structure the template used |
| `htf_alignment` | 20 | Higher timeframes that agree |
| `session_fit` | 15 | Module 04 sessions and kill zones |
| `liquidity_context` | 15 | Pools and targets |
| `cot_agreement` | 10 | Module 02 lean. Context only; missing data adds nothing |
| `learning` | 10 | Patterns that passed a forward trial; neutral when none match |
| `data_quality` | deduction | 5 points off per missing input |

Grades: A from 75, B from 60, C from 45, D from 25, F below that.

The analysis also lists positive, negative, conflicting and missing evidence and the quality deductions for each
candidate. An AI verdict can only lower the score, and an unavailable review caps it at
`ai_review.unavailable_confidence_cap` (default 70). A score records agreement, so 80 does not mean an 80% chance.
Calibration is reported only after `min_outcomes_for_calibration` resolved outcomes (default 100).

## COT

COT positioning compares commercials with large speculators over rolling windows, and flags extremes where history
allows. It respects publication dates. It is context and never a signal alone. The master prompt expected US30 to have
no mapping, but Module 02 maps it to the CFTC's "DJIA x $5" contract, so US30 uses that real output. An instrument with
no mapping reports COT as unavailable.
