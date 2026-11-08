# Module 05 architecture

```
Modules 01-04 (read only)
   -> decision/adapters.py, context.py     DecisionContext for one symbol at one time
   -> regime.py                            regime and weighted bias
   -> candidates/ (setups, levels, sizing) eight templates, deterministic levels
   -> scoring.py, gates.py                 evidence score and hard gates
   -> ai_reviewer/                         optional review; can only lower
   -> recommendation.py                    conflicts, final decision, rule-based explanation
   -> repository.py, lifecycle.py          stored candidates and status history
   -> service.py                           one service for the API, CLI and agents
   -> outcomes.py, backtest/               labels and historical replay
```

Rules that hold across the module:

- Numbers are deterministic. The LLM never supplies a price, a size or a direction.
- Each decision saves the inputs' hash and the config fingerprint, so a result can be traced.
- A missing input is reported, never defaulted.
- Failures are safe: an AI failure leaves the candidate published, capped and unpromoted.
- A final human decision is frozen. Later runs cannot overwrite it.

The frontend reads through `aurum-frontend/src/services/decisionService.ts`. In mock mode it returns empty results, so
the UI never shows an invented setup.
