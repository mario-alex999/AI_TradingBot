# Market conditions and scores

## Condition tags (`conditions.py`)
Several tags can apply at once. Each tag carries:
- `confidence`: a rule-based weight from 0 to 1, **not** a probability;
- `evidence`: a list of `{source, fact}`, where `source` names the module and the measure;
- `active`: true when `confidence >= scoring.conditions.min_confidence` (0.35).

| Tag | Evidence used |
|---|---|
| TRENDING | H1 Kaufman efficiency ratio (20) ≥ 0.35 (+0.45); Module 03 external structure trending (+0.45) |
| RANGING | efficiency ratio ≤ 0.20 (+0.45); Module 03 external structure ranging (+0.45); volatility contraction (+0.15) |
| HIGH_VOLATILITY | regime HIGH (+0.55) or VERY_HIGH (+0.7); abnormal movement (+0.3); volatility expansion (+0.15) |
| LOW_VOLATILITY | regime LOW (+0.55) or VERY_LOW (+0.7) |
| EVENT_DRIVEN | event-risk state RELEASE_WINDOW / POST_EVENT_VOLATILITY (+0.8) or HIGH_EVENT_RISK (+0.6); EVENT_APPROACHING (+0.3) |
| SESSION_TRANSITION | a session opens or closes within `transition_minutes` (30) (+0.5); inside an overlap or transition window (+0.4) |
| LIQUIDITY_EXPANSION | each of up to 3 sweeps, breakouts or range expansions in the last `liquidity_lookback_hours` (6) (+0.35) |
| UNCERTAIN | Module 03 structure transitioning (+0.3) or unavailable (+0.15); Module 03 timeframes disagree (+0.3); volatility unknown (+0.2); trend and range evidence both ≥ 0.45 (+0.4) |

Confidence is the sum of the listed contributions, capped at 1.

When Module 03 is unavailable, its evidence is simply absent, and the tag can still be decided from Module 04's own measures.

## Scores (`scoring.py`, methodology 4.0.0)
Scores are 0–100 indices. Every component is listed with its value, weight and availability. Missing components are **excluded**, never counted as zero. They are not probabilities and not trade signals.

### Event-risk score
```
base     = importance_weight(event) × time_factor(minutes to release)
           importance: high 1.0, medium 0.6, low 0.3
           time factor (pre-release):  ≤0 → 1.0, ≤5 → 0.95, ≤15 → 0.85, ≤30 → 0.75, ≤60 → 0.6, ≤240 → 0.35, ≤1440 → 0.15
           time factor (post-release): 5 → 0.9, 15 → 0.75, 30 → 0.6, 60 → 0.4
modifier = 1 + Σ w_i × (2 v_i − 1)   over the available components
           historical_sensitivity  v = clamp(mean post-release range ratio / 2), w = 0.15
           current_volatility      v = ATR percentile / 100,                  w = 0.15
score    = min(100, 100 × base × modifier)        (range 70%–130% of base)
```
The base is the maximum over the instrument's active events. When the event-risk state is `UNKNOWN`, the score is `null`: there is no number without calendar coverage.

### Session-activity score
A weighted mean of the available components:
- `range_vs_history` (0.5): the latest completed range of the active session / its 20-session mean, ÷ 2, clamped to 0–1;
- `volatility_percentile` (0.3);
- `overlap_history` (0.2): the measured activity ratio of the active overlap, ÷ 2.

### Volatility score
The ATR percentile.

Every score object carries `methodology_version`. Change the version whenever weights or formulas change, so that persisted scores remain interpretable.
