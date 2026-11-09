# Module 06 risk formulas

All arithmetic uses Python `Decimal`, parsed exactly. Floats are converted through their shortest repr, and NaN,
infinity and booleans are rejected. Values become floats only in the JSON response, after the calculation is finished.
Money shown to people is rounded half-up to 0.01. **Volumes are always floored, never rounded.**

## 1. Value of one price unit per lot (VPU)
VPU is the account-currency value of a 1.0 price move on 1.0 lot. It is taken from the broker specification stored by
Module 01 (`instrument_specs`, including the raw `symbol_info`).

| Case | VPU | Notes |
|---|---|---|
| Calc mode 0, 2, 4 or 5 (forex, futures, CFD leverage, CFD index), profit currency = account currency | `contract_size` | Cross-checked: the broker tick value must equal `tick_size × contract_size` within 1%, or the spec is rejected (`SPEC_INCONSISTENT`) |
| Same modes, different profit currency | `contract_size × rate(profit → account)` | The rate must be at most 30 min old (`conversion_max_age_minutes`) |
| ... no fresh rate, but the spec is fresh | `broker_tick_value / tick_size` | Note added to the result |
| ... neither | none | `CONVERSION_UNAVAILABLE` → R003 error → **ERROR** |
| Any other calc mode | `broker_tick_value / tick_size` | Needs a tick value (`SPEC_INCOMPLETE` otherwise); cross-currency also needs a fresh spec |

The broker's `trade_tick_value_loss` is preferred over `trade_tick_value`, because it is the value applied to losses.

## 2. Costs per lot
```
stop_distance   = |entry − stop|
spread_cost     = spread_price × VPU                     (current quote spread; median spread if no quote)
slippage_price  = max_slippage_points[symbol] × point
cost_per_lot    = spread_cost + slippage_price × VPU + commission_per_lot
loss_at_stop    = stop_distance × VPU
total_loss/lot  = loss_at_stop + cost_per_lot
```

## 3. Position size
```
used_risk_pct   = min(requested or configured risk %, max_risk_per_trade_pct)
risk_amount     = equity × used_risk_pct / 100            (equity, or balance if loss_basis = balance)
raw_volume      = risk_amount / total_loss_per_lot
volume          = floor_to_step(min(raw_volume, volume_max), volume_step)
recommended     = volume if volume ≥ volume_min else none   → R018 BLOCK "minimum lot exceeds budget"
min_lot_risk %  = volume_min × total_loss_per_lot / equity × 100
estimated_loss  = final_volume × total_loss_per_lot      (always ≤ risk_amount when the recommended size is used)
```
- If a volume is requested, it must be valid for the broker (minimum, maximum, step) and its estimated loss must fit the
  budget (R019).
- If the budget allows more than `volume_max`, the size is capped and R020 warns.

## 4. Reward-to-risk
```
gross_rr        = |take_profit − entry| / stop_distance
net_reward/lot  = |take_profit − entry| × VPU − spread_cost − commission
adjusted_rr     = net_reward_per_lot / total_loss_per_lot   → R016 needs ≥ min_risk_reward
```

## 5. Margin estimate (approximate)
```
notional = volume × entry × VPU                (formula calc modes only)
margin   = notional / account leverage         (modes 0 and 4)
         = notional                            (modes 2 and 5, conservative: no leverage applied)
projected margin use % = (current margin + margin estimate) / equity × 100   → R037
```
This is an estimate. The broker's own margin rules (tiers, hedging, currency) can differ. See the limitations document.

## 6. Account limits
```
period loss      = start_value − current_value + net_deposits_after_start    (deposits and withdrawals are not P/L)
period loss %    = period loss / start_value × 100       → R030 day, R031 week, R032 month
drawdown %       = (peak_equity − equity) / peak_equity × 100                 → R033
```
- The value is equity, including floating profit and loss, unless `loss_basis: balance`.
- Only deposits and withdrawals made after the start snapshot count; cash moved before it is already in the start value.
- A profitable closed trade cannot hide a floating loss on the equity basis.
- The peak is the highest equity point the engine has seen.
- **Consecutive losses** are counted from the most recent closed trade backwards. A trade within
  `breakeven_tolerance_pct` of equity is breakeven: it neither counts nor resets the streak. Duplicate broker records
  are counted once.

## 7. Portfolio
```
position risk     = |open_price − stop| × VPU × volume   (no stop → unbounded → R041 BLOCK)
reservation risk  = estimated_loss of an unexpired PASS/REVIEW assessment
projected total % = (Σ position risk + Σ reservation risk + this trade's estimated loss) / equity × 100   → R041
```
The same projection is made per instrument (R042) and per asset class (R043).

**Correlation.** Pearson on H1 log returns, computed only over timestamps both series share. The default window is 500
bars and at least 120 shared samples are required. The data must be no older than 72 hours.
- An existing exposure counts as correlated when the new trade makes the pair move the same way:
  - `|r| ≥ threshold` (0.7) with the same direction and positive r; or
  - opposite directions with negative r.
- The projected correlated risk is checked against R044.
- An unreliable estimate is assumed correlated (`unreliable_policy: assume_correlated`) and R045 warns.

## 8. Worked example (EURUSD, verified by test)
Inputs: equity 10,000 USD, risk 1%; BUY entry 1.10010, stop 1.09810, take-profit 1.10510. Contract 100,000; spread
0.00012; slippage 5 points; no commission.

```
VPU            = 100,000
loss_at_stop   = 0.00200 × 100,000                 = 200.00
spread_cost    = 0.00012 × 100,000                 = 12.00
slippage       = 0.00005 × 100,000                 = 5.00
total/lot      = 217.00
risk_amount    = 10,000 × 1%                       = 100.00
raw_volume     = 100 / 217                         = 0.460829…
volume         = floor to 0.01                     = 0.46 lots
estimated_loss = 0.46 × 217                        = 99.82   (0.9982% of equity)
net reward/lot = 0.00500 × 100,000 − 12            = 488
adjusted R:R   = 488 / 217                         = 2.249  (gross 2.5)
```
A 2,000-case property test checks that the estimated loss never exceeds the budget and that the volume is never above
the raw size.
