"""Parse CFTC records (Socrata JSON or history-file rows) into a consistent internal model.

Nothing is inferred: a field that is absent or unparsable stays ``None`` and is reported in
``ParsedReport.problems`` so the quality service can log it. The original record is kept verbatim in
``ParsedReport.payload`` (stored as ``cot_reports.source_payload``) for traceability.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from aurum.cot.fields import CATEGORY_FIELDS, LONG, META_FIELDS, SHORT, SPREAD, lookup, normalise_record


@dataclass
class ParsedPosition:
    category: str
    long: int | None
    short: int | None
    spreading: int | None
    has_spreading_field: bool

    @property
    def net(self) -> int | None:
        return None if self.long is None or self.short is None else self.long - self.short


@dataclass
class ParsedReport:
    report_type: str
    contract_code: str | None
    contract_name: str | None
    report_date: date | None
    open_interest: int | None
    source_record_id: str | None
    positions: dict[str, ParsedPosition]
    payload: dict[str, Any]
    payload_hash: str
    problems: list[dict[str, Any]] = field(default_factory=list)   # {issue_type, severity, description, field}


def parse_number(value: Any) -> tuple[int | None, str | None]:
    """Return (int, None) or (None, reason). Thousands separators/whitespace allowed; decimals must be integral."""
    if value is None:
        return None, "missing"
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if math.isnan(value):
            return None, "missing"
        return (int(value), None) if value.is_integer() else (None, "invalid")
    s = str(value).strip().replace(",", "").replace(" ", "")
    if s in ("", ".", "-", "NA", "N/A", "nan", "NaN", "None"):
        return None, "missing"
    try:
        f = float(s)
    except ValueError:
        return None, "invalid"
    return (int(f), None) if f.is_integer() else (None, "invalid")


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for cut in (10,):
        try:
            return date.fromisoformat(s[:cut])
        except ValueError:
            pass
    return None


def values_hash(code: str | None, rdate: date | None, oi: int | None, positions: dict[str, "ParsedPosition"]) -> str:
    """Hash of the CFTC *values* (not field spellings), so the same report from the API or the history files
    hashes identically and only a genuine CFTC revision of the numbers is detected as a change."""
    canon = [code, str(rdate), oi] + [[c, p.long, p.short, p.spreading] for c, p in sorted(positions.items())]
    return hashlib.sha256(json.dumps(canon, default=str).encode()).hexdigest()


def _clean_payload(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in record.items():
        if isinstance(v, float) and math.isnan(v):
            v = None
        elif hasattr(v, "item"):          # numpy scalar
            v = v.item()
        out[str(k)] = v
    return out


def parse_record(report_type: str, record: dict[str, Any]) -> ParsedReport:
    if report_type not in CATEGORY_FIELDS:
        raise ValueError(f"Unknown report type '{report_type}'")
    payload = _clean_payload(record)
    n = normalise_record(payload)
    problems: list[dict[str, Any]] = []

    def meta(name: str) -> Any:
        found, v = lookup(n, META_FIELDS[name])
        if not found and name != "record_id":
            problems.append({"issue_type": "missing_field", "severity": "error", "field": META_FIELDS[name][0],
                             "description": f"Required field '{META_FIELDS[name][0]}' is absent from the source record."})
        return v

    code_raw, name_raw, date_raw = meta("contract_code"), meta("contract_name"), meta("report_date")
    code = str(code_raw).strip() if code_raw not in (None, "") else None
    rdate = parse_date(date_raw)
    if date_raw is not None and rdate is None:
        problems.append({"issue_type": "invalid_value", "severity": "error", "field": "report_date",
                         "description": f"Report date '{date_raw}' is not a valid date."})
    oi, why = parse_number(meta("open_interest"))
    if why == "invalid":
        problems.append({"issue_type": "invalid_value", "severity": "error", "field": "open_interest",
                         "description": "Open interest is not a whole number."})
    elif why == "missing" and "open_interest_all" in n:
        problems.append({"issue_type": "missing_value", "severity": "warning", "field": "open_interest",
                         "description": "Open interest value is empty."})

    positions: dict[str, ParsedPosition] = {}
    for cat, measures in CATEGORY_FIELDS[report_type].items():
        vals: dict[str, int | None] = {}
        for measure in (LONG, SHORT, SPREAD):
            if measure not in measures:
                vals[measure] = None
                continue
            found, raw = lookup(n, measures[measure])
            if not found:
                problems.append({"issue_type": "missing_field", "severity": "warning", "field": measures[measure][0],
                                 "description": f"{cat} {measure} field '{measures[measure][0]}' is absent."})
                vals[measure] = None
                continue
            v, why = parse_number(raw)
            if why:
                problems.append({"issue_type": "missing_value" if why == "missing" else "invalid_value",
                                 "severity": "warning" if why == "missing" else "error",
                                 "field": measures[measure][0],
                                 "description": f"{cat} {measure} value {raw!r} is {why}."})
            vals[measure] = v
        positions[cat] = ParsedPosition(cat, vals[LONG], vals[SHORT], vals[SPREAD], SPREAD in measures)

    rid = meta("record_id")
    source_record_id = str(rid) if rid not in (None, "") else (f"{report_type}:{code}:{rdate}" if code and rdate else None)
    return ParsedReport(report_type=report_type, contract_code=code,
                        contract_name=str(name_raw).strip() if name_raw is not None else None,
                        report_date=rdate, open_interest=oi, source_record_id=source_record_id,
                        positions=positions, payload=payload,
                        payload_hash=values_hash(code, rdate, oi, positions), problems=problems)
