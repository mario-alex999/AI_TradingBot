# Module 06 configuration guide

## Where the configuration lives
- `config/risk.yaml` is the **seed**. On first use the engine stores it as version 1 in `risk_config_versions`.
- After that, the **database is the source of truth**. Edits to the YAML file do not change a running system. Change the
  configuration through the dashboard (Risk Management → Settings), `PUT /api/v1/risk/config` or
  `python -m aurum.cli risk config set`.
- Each change creates a new, immutable version that records who made it, why, and which limits it loosened. Postgres
  triggers forbid updating or deleting versions.
- Every assessment records the version it used. An assessment made under an older version fails `validate`, so it must
  be re-run.

## Profiles

| Setting | conservative | moderate (default) | aggressive |
|---|---|---|---|
| `risk_per_trade_pct` | 0.5 | 1 | 2 |
| `max_daily_loss_pct` | 2 | 3 | 5 |
| `max_weekly_loss_pct` | 4 | 6 | 10 |
| `max_monthly_loss_pct` | 6 | 10 | 15 |
| `max_drawdown_pct` | 6 | 10 | 20 |
| `max_open_positions` | 3 | 5 | 8 |
| `max_total_open_risk_pct` | 1.5 | 3 | 6 |
| `max_consecutive_losses` | 2 | 3 | 5 |
| `min_risk_reward` | 2.0 | 1.5 | 1.2 |
| `max_margin_utilization_pct` | 20 | 30 | 50 |

Applying a profile replaces those values. Editing individual values afterwards gives profile `custom`.

## Shared settings (`base:` in the YAML)

| Setting | Default | Purpose |
|---|---|---|
| `max_risk_per_trade_pct` | 2 | Hard cap on any requested risk % |
| `max_symbol_risk_pct` / `max_asset_class_risk_pct` / `max_correlated_risk_pct` | 2 / 3 / 2 | Portfolio concentration (R042–R044) |
| `max_trades_per_day` | 6 | R036 |
| `min_free_margin` | 0 | R037; 0 disables the absolute check |
| `max_slippage_points` | EURUSD 5, GBPUSD 8, XAUUSD 50, US30 20, default 0 | Added to the cost of every lot |
| `commission_per_lot` | default 0 | Per-lot, in the account currency |
| `max_spread_points` | EURUSD 40, GBPUSD 60, XAUUSD 600, US30 60 | R052 cap |
| `max_spread_median_multiple` | 3 | R052: spread above 3× its 500-bar median is abnormal |
| `min_stop_spread_multiple` | 2 | R014: stop distance must be at least 2× the spread |
| `atr_check` | H1, 0.25–6 ATR | R015 warning only; never moves a stop |
| `loss_basis` | equity | `equity` includes floating P/L; `balance` does not |
| `breakeven_tolerance_pct` | 0.05 | Trades within this share of equity are breakeven |
| `trading_day` | America/New_York 17:00 | Daily, weekly and monthly period boundaries |
| `period_start_tolerance_minutes` | 120 | How close a snapshot must be to a boundary (R034 otherwise) |
| `reset_policy` | day, week, month: `next_period`; drawdown and consecutive losses: `manual` | When automatic locks end |
| `consecutive_loss_cooldown_minutes` | 0 | Optional expiry for streak locks (0 = manual release only) |
| `assessment_ttl_minutes` | 15 | How long a PASS/REVIEW stays valid and reserves risk |
| `account_max_age_minutes` / `quote_max_age_minutes` | 10 / 5 | Staleness limits (R001 / R051) |
| `spec_max_age_hours` / `conversion_max_age_minutes` | 72 / 30 | R002 / R003 |
| `correlation` | H1, 500 bars, minimum 120, threshold 0.7, maximum age 72 h, `assume_correlated` | R044 / R045 |
| `news` | block HIGH_EVENT_RISK, RELEASE_WINDOW; warn EVENT_APPROACHING, POST_EVENT_VOLATILITY; unknown → review | R053 (Module 04 states) |
| `volatility` | block VERY_HIGH; warn HIGH, VERY_LOW; unknown → review | R054 (Module 04 regimes) |
| `sessions` | none | R055: `blocked_sessions` per symbol, `blocked_weekdays` |
| `data_unavailable_policy` | review | What missing market data means: `review`, `block` or `error`, never `pass` |
| `rules` | `{}` | Per-rule overrides (below) |

The schema is strict (`extra="forbid"`), so a misspelt key is rejected rather than ignored. Cross-field checks:
- `risk_per_trade_pct` must not exceed `max_risk_per_trade_pct` or `max_total_open_risk_pct`;
- `atr_check.min_stop_atr` must be below `max_stop_atr`;
- `correlation.min_samples` must not exceed `lookback_bars`;
- `reset_policy` accepts only its five known keys.

## Rule overrides
```yaml
rules:
  R015: { severity: HARD_BLOCK }    # make the ATR check blocking
  R046: { enabled: false }          # stop warning about same-direction exposure
```
- An override can only make a rule **stricter**: WARNING → HARD_BLOCK is allowed; HARD_BLOCK → WARNING is ignored.
- These rules can never be disabled: **R001, R002, R003, R010, R011, R018, R019, R041, R060**. A change that tries is
  rejected with `RULE_NOT_DISABLEABLE`.

## Changing the configuration safely
Every change requires:
1. `base_version` equal to the current version. A stale edit gets 409 `VERSION_CONFLICT`.
2. A reason of at least 5 characters.
3. `acknowledge_weakening: true` if any limit is loosened. Without it the change returns 409
   `WEAKENING_NOT_ACKNOWLEDGED`. Its message, and `error.details`, list each loosened field, for example
   `max_daily_loss_pct: 3.0 -> 5.0`. The dashboard shows that list in its confirmation dialog. Loosening
   includes:
   - a higher limit, a lower minimum, or a wider spread/slippage/ATR band;
   - removing a blocked state;
   - softening a policy, or a disabled rule;
   - switching the loss basis to `balance`.

CLI equivalents:
```powershell
python -m aurum.cli risk config show
python -m aurum.cli risk config set --reason "tighten per-trade risk" --changes '{"risk_per_trade_pct": 0.5}'
python -m aurum.cli risk config set --reason "switch profile" --profile conservative
python -m aurum.cli risk config set --reason "more daily room" --changes '{"max_daily_loss_pct": 4}' --acknowledge-weakening
```

A configuration change never releases an active lock.

## Environment
| Variable | Purpose |
|---|---|
| `AURUM_ADMIN_API_KEY` | Required for every admin endpoint (assess, config, locks, inbound events). Empty disables them (503) |
| `RISK_EVENTS_WEBHOOK_URL` | Default target for `risk events dispatch`, e.g. `http://localhost:5678/webhook/aurum-risk-events` |
