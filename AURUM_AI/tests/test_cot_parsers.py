"""Parsing (real CFTC sample rows + synthetic), category normalisation and record-level validation."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aurum.cot import validation as V
from aurum.cot.parsers import parse_number, parse_record
from cot_helpers import FIXTURES, cot_cfg, make_record


def real_rows(kind: str) -> list[dict]:
    return pd.read_csv(FIXTURES / f"{kind}_2025_sample.csv", dtype=str, keep_default_na=False).to_dict("records")


@pytest.mark.parametrize("kind,code,cats", [
    ("legacy", "099741", {"commercial", "non_commercial", "non_reportable", "total_reportable"}),
    ("tff", "099741", {"dealer", "asset_manager", "leveraged_funds", "other_reportable", "non_reportable", "total_reportable"}),
    ("disaggregated", "088691", {"producer_merchant", "swap_dealer", "managed_money", "other_reportable",
                                 "non_reportable", "total_reportable"}),
])
def test_real_cftc_rows_parse_completely_and_satisfy_identities(kind, code, cats):
    """Real rows from the official 2025 history files: every field found, no problems, accounting identity holds."""
    mapping = cot_cfg().mappings_for("EURUSD" if code == "099741" else "XAUUSD", kind)[0]
    for row in real_rows(kind):
        rep = parse_record(kind, row)
        assert rep.contract_code == code and rep.report_date and rep.report_date.year == 2025
        assert set(rep.positions) == cats
        assert rep.problems == []
        assert all(p.long is not None and p.short is not None for p in rep.positions.values())
        issues, storable = V.validate_report(rep, mapping)
        assert storable and issues == [], issues
        assert len(rep.payload) == len(row)                       # original record kept verbatim


def test_spreading_only_where_the_family_reports_it():
    rep = parse_record("disaggregated", real_rows("disaggregated")[0])
    assert rep.positions["producer_merchant"].spreading is None and not rep.positions["producer_merchant"].has_spreading_field
    assert rep.positions["managed_money"].spreading is not None
    assert parse_record("legacy", real_rows("legacy")[0]).positions["commercial"].spreading is None


def test_socrata_spelling_including_cftc_typo_is_normalised():
    rep = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 7_000, socrata=True))
    assert rep.problems == []
    assert rep.report_date == date(2026, 1, 6)
    assert rep.positions["non_commercial"].net == 7_000 and rep.positions["non_commercial"].spreading == 5_000
    assert rep.source_record_id == "260106099741F"


def test_same_report_from_api_and_files_hashes_identically():
    a = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 7_000, socrata=True))
    b = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 7_000))
    assert a.payload_hash == b.payload_hash
    c = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 7_001))
    assert c.payload_hash != a.payload_hash


@pytest.mark.parametrize("raw,expected", [("1,234", (1234, None)), (" 56 ", (56, None)), (7.0, (7, None)),
                                          ("", (None, "missing")), (".", (None, "missing")), (None, (None, "missing")),
                                          ("abc", (None, "invalid")), ("1.5", (None, "invalid")), (True, (None, "invalid"))])
def test_numeric_conversion(raw, expected):
    assert parse_number(raw) == expected


def test_missing_and_invalid_values_are_reported_not_inferred():
    rec = make_record("tff", "099741", date(2026, 1, 6))
    rec["Lev_Money_Positions_Long_All"] = ""
    rec["Dealer_Positions_Short_All"] = "n/a!"
    del rec["Asset_Mgr_Positions_Spread_All"]
    rep = parse_record("tff", rec)
    assert rep.positions["leveraged_funds"].long is None and rep.positions["leveraged_funds"].net is None
    kinds = {(p["issue_type"], p["field"]) for p in rep.problems}
    assert ("missing_value", "lev_money_positions_long_all") in kinds
    assert ("invalid_value", "dealer_positions_short_all") in kinds
    assert ("missing_field", "asset_mgr_positions_spread_all") in kinds


def test_record_without_date_is_rejected_but_payload_kept():
    rec = make_record("legacy", "099741", date(2026, 1, 6))
    rec.pop("As of Date in Form YYYY-MM-DD")
    rep = parse_record("legacy", rec)
    issues, storable = V.validate_report(rep, None)
    assert not storable
    rej = [i for i in issues if i.issue_type == "rejected_record"][0]
    assert rej.details["payload"]["CFTC Contract Market Code"] == "099741"


def test_contract_name_mismatch_negative_and_zero_open_interest():
    m = cot_cfg().mappings_for("EURUSD", "legacy")[0]
    rec = make_record("legacy", "099741", date(2026, 1, 6))
    rec["Market and Exchange Names"] = "EURO FX - SOMEWHERE ELSE"
    rec["Open Interest (All)"] = "-5"
    issues, ok = V.validate_report(parse_record("legacy", rec), m)
    types = {i.issue_type: i.severity for i in issues}
    assert ok and types["unexpected_contract_name"] == "warning" and types["invalid_open_interest"] == "error"
    rec["Open Interest (All)"] = "0"
    assert "zero_open_interest" in {i.issue_type for i in V.validate_report(parse_record("legacy", rec), m)[0]}


def test_inconsistent_long_short_spreading_totals_flagged():
    rec = make_record("disaggregated", "088691", date(2026, 1, 6))
    rec["M_Money_Positions_Long_All"] = str(int(rec["M_Money_Positions_Long_All"]) + 50_000)
    issues, ok = V.validate_report(parse_record("disaggregated", rec), None)
    assert ok and any(i.issue_type == "inconsistent_totals" and i.severity == "warning" for i in issues)


def test_duplicates_identical_and_conflicting():
    a = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 100))
    b = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 100))
    c = parse_record("legacy", make_record("legacy", "099741", date(2026, 1, 6), 999))
    kept, issues = V.duplicate_check([a, b, c])
    assert len(kept) == 1 and kept[0] is a
    assert [i.severity for i in issues] == ["warning", "error"]


def test_schema_change_detected_and_unexpected_extra_fields_tolerated():
    good = [make_record("tff", "099741", date(2026, 1, 6)) | {"Brand_New_Field": "1"}]
    assert V.schema_check("tff", good) == []
    broken = [{k: v for k, v in good[0].items() if "Lev_Money" not in k}]
    iss = V.schema_check("tff", broken)
    assert iss and iss[0].severity == "critical" and "lev_money_positions_long_all" in iss[0].details["missing_fields"]


def test_series_gap_stale_and_unusual_change_checks():
    from datetime import datetime, timedelta, timezone
    gaps = V.series_checks([date(2026, 1, 6), date(2026, 1, 13), date(2026, 2, 10)])
    assert len(gaps) == 1 and gaps[0].details["approx_missing"] == 3
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert V.stale_check(now - timedelta(days=20), now, 10).issue_type == "stale_data"
    assert V.stale_check(now - timedelta(days=2), now, 10) is None
    assert V.stale_check(None, now, 10).severity == "error"
    nets = [1000 + (i % 3) * 10 for i in range(40)] + [90_000]
    dates = [date(2025, 1, 7) + timedelta(weeks=i) for i in range(41)]
    unusual = V.unusual_change_check(dates, nets)
    assert unusual and unusual[-1].severity == "info"      # reported, never rejected
