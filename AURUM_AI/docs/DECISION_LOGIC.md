# Module 05 decision logic

`aurum/decision/recommendation.py::decide`, checked in this order:

1. **INSUFFICIENT_DATA** when `insufficient_after_missing` inputs are missing, or every candidate failed the data
   quality gate.
2. **NO_TRADE** when candidates exist and none passes (market gate, other gate, or AI rejection).
3. **WAIT** when no template found an entry.
4. **WAIT** when the best candidate needs alignment and the timeframes disagree, or it runs against the bias.
5. **WAIT** when its grade is outside `decision.min_grades` (default A, B).
6. **BUY_SETUP / SELL_SETUP** otherwise.
7. **STRONG_** only at the strong grade, with the bias, no gate warning, no conflict and no AI caution.

A high score alone never gives a setup, and an empty result is a valid outcome.

## Regime

`regime.py` gives one of BULLISH_TREND, BEARISH_TREND, RANGE, CONSOLIDATION, VOLATILITY_EXPANSION,
VOLATILITY_CONTRACTION, BREAKOUT, POTENTIAL_REVERSAL or UNCLEAR, with the evidence that supports and conflicts with it.

POTENTIAL_REVERSAL needs a confirmed CHoCH at most `regime.reversal_max_age_candles` old (default 8). Module 03's
`transitioning` state is not enough alone: it also fires when the swing sequence lags the bias, which on real data was
about 90% of sampled hours. That case keeps the trend label and is listed as conflicting evidence. The default of 8 is a
judgement, not a tested value.

## Bias

Each timeframe votes with its weight (D1 5, H4 4, H1 3, M15 2, M5 1). The overall bias is the leading side when it holds
at least `min_agreement` of the known weight (default 0.6), `mixed` when it does not, and `unknown` when too few
timeframes are usable. Timeframes do not need to be unanimous.

## Gates

`data_quality`, `event_risk`, `market_open`, `spread_volatility`, `risk_reward`, `structure_conflict`, `account_risk`,
`duplication`, `cot`. A failed gate blocks approval. Warnings stay visible on the candidate.

## Human review

Candidates start as GENERATED and need a person. Approval means APPROVED_FOR_FURTHER_PROCESSING and places nothing.
A person's re-open outranks a cached AI rejection. A stop broken before review gives INVALIDATED.
