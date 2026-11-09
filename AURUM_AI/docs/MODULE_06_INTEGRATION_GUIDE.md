# Module 06 integration guide

Module 06 sits between idea generation (Module 05) and anything that could lead to an order (Modules 07 and 09). It
only **reads** the earlier modules and only **publishes decisions**.

## Inputs from Modules 01–05 (read-only)

| Source | What Module 06 reads | If it is missing |
|---|---|---|
| Module 01 market data | `instruments`, `instrument_specs` (contract size, tick size/value, calc mode, stops level, volume min/max/step, raw `symbol_info`), `market_ticks` (falls back to the last M5 candle), M5 spreads for the median, H1 closes for correlation, `data_quality_issues` (last 24 h) | R002 BLOCK (spec missing or stale); R051/R052 per policy; correlation unreliable → assumed correlated |
| Account mirror (`sync-account`) | `account_snapshots` (DEMO only), `account_deals` (deposits and withdrawals), `trade_records` (open positions, closed trades) | R001 **ERROR**; no assessment can PASS |
| Module 02 COT | via Module 04's `integrations.module_02` | Informational only (R070) |
| Module 04 intelligence | `MarketIntelligenceService.analyze()`: `sessions.market_open`, `event_risk.state` and coverage, `volatility.regime` and H1 ATR | R050/R053/R054 follow `unknown_policy` (REVIEW by default) and say "NOT checked" |
| Module 05 decision engine | `TradeCandidate` by id: direction, entry zone, stop, target, strategy, timeframe, confidence | 422 `INVALID_PROPOSAL` |

**Module 05 confidence never changes the decision.** It is stored as R071 (informational). A test sends a candidate with
confidence 1.0 and a stop on the wrong side; the result is BLOCK.

## Module 07 (human review): the contract
Every assessment carries a `module07` block. Module 07 **must**:
1. Offer review only when `eligibleForHumanReview` is true, that is, decision PASS or REVIEW.
2. Treat `hardBlock: true` (BLOCK) and ERROR as final. They cannot be approved, overridden or "accepted anyway".
3. When `mustDisplayWarnings` is true (REVIEW), show every item in `warnings` with its `message`. The reviewer must
   accept them explicitly.
4. Show `finalVolume`, `monetaryRisk`, `riskPct`, `riskReward`, `expiresAt` and the `disclaimer` as given, without
   recalculating them.
5. Before recording an approval, call `POST /risk/assessments/{id}/validate`. If `valid` is false (expired, consumed,
   cancelled, config changed, or a lock now applies), request a new assessment.

Module 07 cannot change limits. Configuration changes go through `PUT /risk/config` with a reason, and are audited.

## Module 08 / n8n (orchestration)
- **Pull:** `GET /risk/events?after=<last sequence>` on a schedule. Store the last sequence you processed.
- **Push:** `python -m aurum.cli risk events dispatch --url http://localhost:5678/webhook/aurum-risk-events`.
  - Each event is POSTed as JSON.
  - Failures back off from 15 s up to 1 h; after 8 attempts the event is `dead` and still visible in the outbox.
  - Events carry `idempotencyKey`, so n8n can deduplicate.
- **Into Module 06:** `POST /risk/events/inbound` (admin key as an n8n Header Auth credential). `eventId` must be unique;
  a repeat is acknowledged as `duplicate: true` and not processed again.
- **To request an assessment:** call `POST /risk/assess` with an `Idempotency-Key` derived from the workflow run, so a
  retried run gets the same answer instead of a second reservation.
- n8n **cannot** modify a decision, release a lock without the admin key and a reason, or reach MT5. Setup and starter
  workflows: [N8N_SETUP.md](N8N_SETUP.md).

## Module 09 (execution, future): required behaviour
Module 06 does not execute, and nothing in it can. A future execution module must:
1. Execute only an assessment whose decision is PASS, or REVIEW explicitly approved in Module 07.
2. Call `POST /risk/assessments/{id}/validate` **immediately before** sending an order. Abort if it is not valid.
3. Use `finalVolume` as the maximum volume. Never increase it, never round it up, never trade without the assessed stop.
4. Call `POST /risk/assessments/{id}/consume` after the order is placed. This prevents re-use and releases the
   reservation once the position itself carries the risk.
5. On failure or abandonment, call `/cancel`.

## Dashboard (aurum-frontend)
The Risk Management page uses only `/api/v1/risk/*` (service `src/services/riskService.ts`, hooks
`src/hooks/useRisk.ts`).
- Admin actions use the in-memory admin key from Settings → Security. They need the `risk:manage` permission, which only
  the administrator role has.
- In demo mode the page shows no risk figures.
- The original informational calculator is kept beside the engine calculator. It is labelled informational and enforces
  nothing.

The older `/api/v1/risk/overview` (account API, monitored `RISK_MAX_*` limits) still exists for compatibility, but the
dashboard no longer uses it. Module 06 is the enforcing engine.
