"""COT data-quality checks. Issues are classified INFO / WARNING / ERROR / CRITICAL and always explained.

Unusual-but-legitimate positioning changes are reported (INFO), never rejected. A record is rejected only
when it cannot be stored meaningfully (no contract code or report date) - its raw payload is still kept in
the issue details so nothing is lost.

Accounting identities used (they hold for all three CFTC futures-only families):
    open interest = sum(long of every non-total category) + sum(spreading)
                  = sum(short of every non-total category) + sum(spreading)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from aurum.cot.config import ContractMapping
from aurum.cot.fields import expected_field_names, normalise_record
from aurum.cot.parsers import ParsedReport

INFO, WARNING, ERROR, CRITICAL = "info", "warning", "error", "critical"
TOTAL_TOLERANCE = 0.005      # 0.5 % of open interest (rounding / late corrections)


@dataclass
class Issue:
    issue_type: str
    severity: str
    description: str
    report_date: date | None = None
    details: dict[str, Any] = field(default_factory=dict)


def validate_report(rep: ParsedReport, mapping: ContractMapping | None) -> tuple[list[Issue], bool]:
    """Record-level checks. Returns (issues, storable)."""
    issues = [Issue(p["issue_type"], p["severity"], p["description"], rep.report_date, {"field": p.get("field")})
              for p in rep.problems]
    storable = bool(rep.contract_code and rep.report_date)
    if not storable:
        issues.append(Issue("rejected_record", ERROR,
                            "Record has no contract code or report date and cannot be stored; raw payload kept here.",
                            rep.report_date, {"payload": rep.payload}))
        return issues, False
    if mapping and rep.contract_name and rep.contract_name.strip().upper() != mapping.cftc_contract_name.strip().upper():
        issues.append(Issue("unexpected_contract_name", WARNING,
                            f"CFTC contract name '{rep.contract_name}' differs from the mapped name "
                            f"'{mapping.cftc_contract_name}' for code {rep.contract_code}. Verify the mapping.",
                            rep.report_date, {"field": "contract_name", "expected": mapping.cftc_contract_name}))
    if rep.open_interest is not None:
        if rep.open_interest < 0:
            issues.append(Issue("invalid_open_interest", ERROR, f"Open interest is negative ({rep.open_interest}).",
                                rep.report_date, {"field": "open_interest"}))
        elif rep.open_interest == 0:
            issues.append(Issue("zero_open_interest", WARNING,
                                "Open interest is zero; % of open interest cannot be calculated for this report.",
                                rep.report_date, {"field": "open_interest"}))
    for cat, p in rep.positions.items():
        for measure, v in (("long", p.long), ("short", p.short), ("spreading", p.spreading)):
            if v is not None and v < 0:
                issues.append(Issue("negative_position", ERROR, f"{cat} {measure} is negative ({v}).",
                                    rep.report_date, {"field": f"{cat}.{measure}"}))
    issues += _identity_checks(rep)
    return issues, True


def _identity_checks(rep: ParsedReport) -> list[Issue]:
    oi = rep.open_interest
    cats = [p for c, p in rep.positions.items() if c != "total_reportable"]
    if not oi or any(p.long is None or p.short is None for p in cats):
        return []
    spreads = sum(p.spreading or 0 for p in cats)
    out = []
    for side, total in (("long", sum(p.long for p in cats) + spreads), ("short", sum(p.short for p in cats) + spreads)):
        diff = total - oi
        if abs(diff) > max(2, TOTAL_TOLERANCE * oi):
            out.append(Issue("inconsistent_totals", WARNING,
                             f"Sum of {side} + spreading positions ({total:,}) differs from open interest ({oi:,}) "
                             f"by {diff:+,}. Stored unchanged; treat this report with caution.",
                             rep.report_date, {"field": f"identity_{side}", "sum": total, "open_interest": oi}))
    tr = rep.positions.get("total_reportable")
    if tr and tr.long is not None:
        nonrep = rep.positions.get("non_reportable")
        if nonrep and nonrep.long is not None and tr.long + nonrep.long != oi and \
                abs(tr.long + nonrep.long - oi) > max(2, TOTAL_TOLERANCE * oi):
            out.append(Issue("inconsistent_totals", WARNING,
                             "Total reportable long + non-reportable long does not equal open interest.",
                             rep.report_date, {"field": "identity_reportable"}))
    return out


def schema_check(report_type: str, records: list[dict[str, Any]]) -> list[Issue]:
    """Detect API/file schema changes: expected fields missing from every record of a batch."""
    if not records:
        return []
    present: set[str] = set()
    for r in records[:50]:
        present.update(normalise_record(r).keys())
    expected = expected_field_names(report_type)
    from aurum.cot.fields import all_known_aliases
    known = all_known_aliases(report_type)
    missing = sorted(e for e in expected if e not in present and not _alias_present(e, report_type, present))
    if missing:
        return [Issue("schema_change", CRITICAL,
                      f"{len(missing)} expected {report_type} field(s) are missing from the source: "
                      f"{', '.join(missing[:8])}. The CFTC may have changed its schema; update aurum/cot/fields.py.",
                      None, {"missing_fields": missing, "known_fields": len(known)})]
    return []


def _alias_present(primary: str, report_type: str, present: set[str]) -> bool:
    from aurum.cot.fields import CATEGORY_FIELDS, META_FIELDS
    for al in META_FIELDS.values():
        if primary in al:
            return any(a in present for a in al)
    for measures in CATEGORY_FIELDS[report_type].values():
        for al in measures.values():
            if primary in al:
                return any(a in present for a in al)
    return False


def duplicate_check(reports: list[ParsedReport]) -> tuple[list[ParsedReport], list[Issue]]:
    """Keep one record per (contract, date). Identical copies -> WARNING, conflicting copies -> ERROR (first kept)."""
    seen: dict[tuple[str | None, date | None], ParsedReport] = {}
    issues: list[Issue] = []
    for r in reports:
        key = (r.contract_code, r.report_date)
        if key in seen:
            same = seen[key].payload_hash == r.payload_hash
            issues.append(Issue("duplicate_record", WARNING if same else ERROR,
                                f"{'Identical' if same else 'Conflicting'} duplicate source record for contract "
                                f"{r.contract_code} on {r.report_date}; the first copy was kept.",
                                r.report_date, {"field": "duplicate", "contract_code": r.contract_code,
                                                "kept_hash": seen[key].payload_hash,
                                                "duplicate_hash": r.payload_hash}))
            continue
        seen[key] = r
    return list(seen.values()), issues


def series_checks(dates: list[date], history_start: date | None = None, max_gap_days: int = 10) -> list[Issue]:
    """Missing weekly observations inside a stored series (gaps longer than ``max_gap_days``)."""
    ds = sorted(set(dates))
    out = []
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > max_gap_days:
            missing = (b - a).days // 7 - 1
            out.append(Issue("missing_reports", WARNING,
                             f"No report between {a} and {b} (~{missing} weekly report(s) missing).", b,
                             {"field": f"gap_{a}", "previous": str(a), "next": str(b), "approx_missing": missing}))
    return out


def stale_check(latest_release: datetime | None, now: datetime, stale_after_days: int) -> Issue | None:
    if latest_release is None:
        return Issue("no_data", ERROR, "No COT reports are stored for this series.", None, {"field": "freshness"})
    age = (now - latest_release).days
    if age > stale_after_days:
        return Issue("stale_data", WARNING,
                     f"Latest stored report was released {age} days ago (threshold {stale_after_days}); "
                     f"a newer CFTC report has probably been published. Run: python -m aurum.cli cot update",
                     None, {"field": "freshness", "age_days": age})
    return None


def unusual_change_check(report_dates: list[date], net: list[float | None], z_threshold: float = 4.0,
                         min_history: int = 26) -> list[Issue]:
    """Flag (INFO) weekly net changes far outside their own history. Never rejects data."""
    s = pd.Series(net, index=report_dates, dtype="float64")
    ch = s.diff()
    out = []
    for i in range(min_history, len(ch)):
        hist = ch.iloc[1:i].dropna()
        v = ch.iloc[i]
        if pd.isna(v) or len(hist) < min_history or hist.std(ddof=0) == 0:
            continue
        z = (v - hist.mean()) / hist.std(ddof=0)
        if abs(z) >= z_threshold:
            out.append(Issue("unusual_change", INFO,
                             f"Weekly net change of {v:+,.0f} is {z:+.1f} standard deviations from its history - "
                             f"legitimate but unusual; kept as reported.", ch.index[i],
                             {"field": "net_change", "z": round(float(z), 2)}))
    return out


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
