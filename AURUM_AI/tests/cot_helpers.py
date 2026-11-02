"""Synthetic CFTC records for Module 02 tests (identity-consistent, in file or Socrata field spelling).

Synthetic data is only used by tests; real CFTC sample rows live in tests/fixtures/cftc/.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aurum.cot.client import FetchResult
from aurum.cot.config import load_cot_config

FIXTURES = Path(__file__).parent / "fixtures" / "cftc"

FILE_COLS = {
    "legacy": {
        "_name": "Market and Exchange Names", "_date": "As of Date in Form YYYY-MM-DD",
        "_code": "CFTC Contract Market Code", "_oi": "Open Interest (All)",
        ("non_commercial", "long"): "Noncommercial Positions-Long (All)",
        ("non_commercial", "short"): "Noncommercial Positions-Short (All)",
        ("non_commercial", "spreading"): "Noncommercial Positions-Spreading (All)",
        ("commercial", "long"): "Commercial Positions-Long (All)",
        ("commercial", "short"): "Commercial Positions-Short (All)",
        ("total_reportable", "long"): " Total Reportable Positions-Long (All)",
        ("total_reportable", "short"): "Total Reportable Positions-Short (All)",
        ("non_reportable", "long"): "Nonreportable Positions-Long (All)",
        ("non_reportable", "short"): "Nonreportable Positions-Short (All)",
    },
    "tff": {
        "_name": "Market_and_Exchange_Names", "_date": "Report_Date_as_YYYY-MM-DD",
        "_code": "CFTC_Contract_Market_Code", "_oi": "Open_Interest_All",
        **{(c, m): f"{p}_Positions_{s}_All" for c, p in (("dealer", "Dealer"), ("asset_manager", "Asset_Mgr"),
                                                          ("leveraged_funds", "Lev_Money"), ("other_reportable", "Other_Rept"))
           for m, s in (("long", "Long"), ("short", "Short"), ("spreading", "Spread"))},
        ("total_reportable", "long"): "Tot_Rept_Positions_Long_All",
        ("total_reportable", "short"): "Tot_Rept_Positions_Short_All",
        ("non_reportable", "long"): "NonRept_Positions_Long_All",
        ("non_reportable", "short"): "NonRept_Positions_Short_All",
    },
    "disaggregated": {
        "_name": "Market_and_Exchange_Names", "_date": "Report_Date_as_YYYY-MM-DD",
        "_code": "CFTC_Contract_Market_Code", "_oi": "Open_Interest_All",
        ("producer_merchant", "long"): "Prod_Merc_Positions_Long_All",
        ("producer_merchant", "short"): "Prod_Merc_Positions_Short_All",
        ("swap_dealer", "long"): "Swap_Positions_Long_All",
        ("swap_dealer", "short"): "Swap__Positions_Short_All",
        ("swap_dealer", "spreading"): "Swap__Positions_Spread_All",
        ("managed_money", "long"): "M_Money_Positions_Long_All",
        ("managed_money", "short"): "M_Money_Positions_Short_All",
        ("managed_money", "spreading"): "M_Money_Positions_Spread_All",
        ("other_reportable", "long"): "Other_Rept_Positions_Long_All",
        ("other_reportable", "short"): "Other_Rept_Positions_Short_All",
        ("other_reportable", "spreading"): "Other_Rept_Positions_Spread_All",
        ("total_reportable", "long"): "Tot_Rept_Positions_Long_All",
        ("total_reportable", "short"): "Tot_Rept_Positions_Short_All",
        ("non_reportable", "long"): "NonRept_Positions_Long_All",
        ("non_reportable", "short"): "NonRept_Positions_Short_All",
    },
}

# Socrata spelling for the legacy family (incl. the CFTC's own "postions" typo)
SOCRATA_LEGACY = {
    "_name": "market_and_exchange_names", "_date": "report_date_as_yyyy_mm_dd", "_code": "cftc_contract_market_code",
    "_oi": "open_interest_all",
    ("non_commercial", "long"): "noncomm_positions_long_all", ("non_commercial", "short"): "noncomm_positions_short_all",
    ("non_commercial", "spreading"): "noncomm_postions_spread_all",
    ("commercial", "long"): "comm_positions_long_all", ("commercial", "short"): "comm_positions_short_all",
    ("total_reportable", "long"): "tot_rept_positions_long_all", ("total_reportable", "short"): "tot_rept_positions_short",
    ("non_reportable", "long"): "nonrept_positions_long_all", ("non_reportable", "short"): "nonrept_positions_short_all",
}

MAIN = {"legacy": "non_commercial", "tff": "leveraged_funds", "disaggregated": "managed_money"}
NAMES = {"099741": "EURO FX - CHICAGO MERCANTILE EXCHANGE", "096742": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
         "088691": "GOLD - COMMODITY EXCHANGE INC.", "124603": "DJIA x $5 - CHICAGO BOARD OF TRADE"}


def make_record(report_type: str, code: str, rdate: date, main_net: int = 10_000, base: int = 100_000,
                cols: dict | None = None, socrata: bool = False) -> dict:
    """One identity-consistent record: main category net = main_net; non_reportable balances the shorts."""
    cols = cols or (SOCRATA_LEGACY if socrata else FILE_COLS[report_type])
    cats = sorted({k[0] for k in cols if isinstance(k, tuple)} - {"total_reportable", "non_reportable"})
    pos: dict[str, dict[str, int]] = {}
    for c in cats:
        has_spread = (c, "spreading") in cols
        if c == MAIN[report_type]:
            pos[c] = {"long": base + max(main_net, 0), "short": base + max(-main_net, 0),
                      "spreading": 5_000 if has_spread else None}
        else:
            pos[c] = {"long": base // 2, "short": base // 2, "spreading": 1_000 if has_spread else None}
    spreads = sum(p["spreading"] or 0 for p in pos.values())
    nr_long = 20_000
    oi = sum(p["long"] for p in pos.values()) + spreads + nr_long
    nr_short = oi - spreads - sum(p["short"] for p in pos.values())
    pos["non_reportable"] = {"long": nr_long, "short": nr_short, "spreading": None}
    pos["total_reportable"] = {"long": oi - nr_long, "short": oi - nr_short, "spreading": None}
    rec = {cols["_name"]: NAMES.get(code, f"CONTRACT {code}"),
           cols["_date"]: f"{rdate.isoformat()}T00:00:00.000" if socrata else rdate.isoformat(),
           cols["_code"]: code, cols["_oi"]: str(oi)}
    for (c, m), col in ((k, v) for k, v in cols.items() if isinstance(k, tuple)):
        v = pos[c][m]
        rec[col] = str(v) if v is not None else ""
    if socrata:
        rec["id"] = f"{rdate:%y%m%d}{code}F"
    return rec


def tuesdays(start: date, n: int) -> list[date]:
    d = start + timedelta(days=(1 - start.weekday()) % 7)
    return [d + timedelta(weeks=i) for i in range(n)]


def series(report_type: str, code: str, start: date, nets: list[int], **kw) -> list[dict]:
    return [make_record(report_type, code, d, n, **kw) for d, n in zip(tuesdays(start, len(nets)), nets)]


def zip_bytes(records: list[dict], member: str = "annual.txt") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, pd.DataFrame(records).to_csv(index=False))
    return buf.getvalue()


class FakeClient:
    """Stands in for CftcClient: returns prepared records per report type (filtered by date like the real one)."""

    def __init__(self, data: dict[str, list[dict]], source: str = "files", fail: set[str] | None = None):
        self.data, self.source, self.fail = data, source, fail or set()
        self.calls: list[tuple] = []
        self.last_fallback_reason = None

    def fetch(self, ds, codes, start, end, progress=None):
        self.calls.append((ds.key, start, end))
        if ds.key in self.fail:
            from aurum.cot.client import CftcHTTPError
            raise CftcHTTPError("simulated outage", 503)
        out = []
        for r in self.data.get(ds.key, []):
            from aurum.cot.parsers import parse_record
            p = parse_record(ds.key, dict(r))
            if p.report_date and start <= p.report_date <= end and p.contract_code in set(codes):
                out.append(dict(r))
        return FetchResult(out, self.source, 1, {})

    def check(self):
        return [{"dataset": "legacy", "source": "files", "ok": True, "file": "x", "bytes": 1}]


def cot_cfg():
    return load_cot_config()
