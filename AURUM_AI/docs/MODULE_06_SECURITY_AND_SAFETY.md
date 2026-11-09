# Module 06 security and safety

## Non-negotiable restrictions and how each is enforced

| Restriction | Enforcement | Test |
|---|---|---|
| Never place, modify or close orders | `aurum/risk` and the risk router contain no trading call and import neither `MetaTrader5` nor the MT5 gateway | `test_no_order_placement_anywhere_in_module_06` scans the source for `order_send`, `order_check`, `positions_get`, `OrderSend`, `TRADE_ACTION` and similar, and checks imports with the AST |
| No execution endpoint | The router exposes only assess, size, read, config, lock and event routes | `test_no_trading_routes_in_risk_api` |
| Module 05 confidence cannot override a hard block | Confidence is recorded only as informational R071; `decide()` ignores informational rules | `test_module_05_confidence_cannot_override_hard_block` |
| Module 07 and n8n cannot override | No endpoint changes a decision. Stored rule rows cannot be updated (trigger). `module07.hardBlock` marks BLOCK and ERROR as final | API tests; append-only trigger |
| No silent approval on missing data | Missing data yields ERROR, or REVIEW/BLOCK/ERROR by policy. `pass` is not an accepted policy value | `test_stale_account_never_passes`, `test_error_is_never_turned_into_pass_by_disabling_rules`, scenario tests (Module 04 down, stale spec, missing quote) |
| No unvalidated instrument specs | `InstrumentSpec.validate()`, the tick value cross-check, and a staleness limit; failure is R002 BLOCK | Calculation tests (inconsistent spec, calc mode 3, incomplete spec) |
| No rounding up | `floor_to_step`; a minimum lot over budget is rejected (R018) | 2,000-case property test; `test_position_size_never_rounded_up` |
| Safety rules cannot be disabled or softened | `NOT_DISABLEABLE`; overrides can only tighten | `test_safety_rules_cannot_be_disabled`, `test_rule_override_can_only_tighten` |
| Locks are not removed by a restart or a config change | Locks are database rows; config updates do not touch them | Scenario tests (restart, config change) |
| No auto-closing of positions | Module 06 has no close path | Source scan |
| DEMO accounts only | A non-DEMO snapshot makes the account `invalid` → R001 ERROR | `test_non_demo_account_invalid` |
| Never store broker passwords; never log keys | Module 06 reads no credentials. The admin key is compared in constant time and never logged. The frontend keeps it in memory only | Frontend test checks browser storage |

## Access control
- **Admin key** (`X-AURUM-Admin-Key`) is required for: assess, config changes, locks, consume/cancel and inbound events.
  - If no key is configured, those endpoints are disabled (503).
  - There are no user accounts or roles on the backend yet. The key is shared, so the `X-AURUM-Actor` header is a label,
    not proof of identity. See the limitations document.
- **Read endpoints are open.** This matches the rest of the API, which binds to `127.0.0.1` by default. Do not expose
  port 8000 to a network without adding authentication in front of it.
- **Rate limit:** 60 per minute on `/assess` and `/position-size`.
- **Input handling:** strict pydantic config schema; exact Decimal parsing that rejects NaN and infinity; symbol
  allow-list; actor header sanitised and length-limited; correlation ids echoed but never interpreted.

## Integrity
- **Atomic writes.** One transaction per assessment.
- **Hash-chained, append-only audit log.** `risk audit-verify` detects any edited row (tested).
- **Serialisation.** A Postgres advisory lock plus a process lock, so concurrent assessments cannot over-commit a limit
  (tested: at most three of many concurrent requests pass when three fit).
- **Idempotency** on `(idempotency_key, attempt)`. A conflicting replay is a 409, not a silent second decision.

## Operational safety notes
- Releasing a loss or drawdown lock does not lift the limit itself while it is still reached.
- Loosening any limit needs a reason **and** an explicit acknowledgement, and is listed in the version history.
- Every result carries a disclaimer. Estimated risk assumes the stop is filled at its price plus the configured
  slippage. Gaps, weekend opens and liquidity can make an actual loss larger.
