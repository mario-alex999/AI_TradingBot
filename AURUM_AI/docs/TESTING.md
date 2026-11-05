# Module 05 testing

```powershell
python -m pytest tests/decision -q       # Module 05
python -m pytest tests -q                # everything
cd ..\aurum-frontend; npm test           # frontend
```

Tests use fake model clients and SQLite, and make no network or LLM call. Two tests are opt-in and skip by default:

| Variable | Test |
|---|---|
| `AURUM_TEST_DATABASE_URL` | Applies the migration to a PostgreSQL database you name |
| `AURUM_LIVE_AI=1` | One real AI call. This spends money |

## Files

| File | Covers |
|---|---|
| `tests/decision/test_decision_rules.py` | Templates, levels, sizing, scoring, gates, lifecycle, regime, bias, decisions |
| `tests/decision/test_decision_ai_reviewer.py` | Request shape, can-only-lower rule, budget, retries, injection, unsupported claims, providers |
| `tests/decision/test_decision_outcomes_backtest.py` | Outcome labels, replay, costs, metrics, look-ahead |
| `tests/decision/test_decision_service_api.py` | Service, API, CLI, approvals, notifications, safety scan |
| `aurum-frontend/src/test/decision.test.tsx` | Approval flow, admin key, AI Engine page, backtest start, no invented data |

## Look-ahead

| Risk | Test |
|---|---|
| Later data changing an earlier decision | `test_appending_future_data_does_not_change_an_earlier_decision` |
| Future candles and structure in Module 03 | `tests/analysis/test_no_lookahead.py`, `tests/analysis/test_multi_timeframe.py` |
| COT before publication | `tests/test_cot_analysis.py::test_report_not_visible_before_release`, `tests/test_cot_release.py` |
| Later COT reports changing history | `tests/test_cot_analysis.py::test_historical_metrics_do_not_change_when_future_reports_arrive` |
| Calendar revisions and actuals before release | `tests/market_intelligence/test_mi_calendar.py` |
| Module 04 levels and analysis | `test_mi_levels_liquidity.py::test_levels_have_no_look_ahead`, `test_mi_api_cli.py::test_analysis_has_no_look_ahead` |
| Official coverage | `test_mi_official.py::test_official_coverage_is_point_in_time` |
| DST | `tests/market_intelligence/test_mi_sessions.py` |

The module's own look-ahead test builds the full context, including regime, bias and decision, on complete and on
truncated data, and requires identical results.

## Safety

`test_decision_service_api.py` scans `aurum/decision/` for broker calls such as `order_send`, `order_check` and
`positions_get`. `tests/test_mt5_gateway.py` still proves the gateway refuses them.
