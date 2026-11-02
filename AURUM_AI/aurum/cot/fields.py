"""Participant categories per report family and the source-field aliases that feed them.

The same CFTC publication is available as Socrata JSON (lower_snake field names, e.g. ``noncomm_positions_long_all``)
and as official history files (e.g. ``Noncommercial Positions-Long (All)``, ``Lev_Money_Positions_Long_All``).
Both are normalised with :func:`norm` (lower case, non-alphanumerics -> ``_``, ``postions`` typo fixed, repeated
underscores collapsed) and matched against the alias sets below. Only "All" (old + other crop years combined)
figures are used. Original field names are preserved in ``cot_reports.source_payload``.
"""
from __future__ import annotations

import re

LONG, SHORT, SPREAD = "long", "short", "spreading"

# family -> category -> measure -> normalised aliases (first match wins)
CATEGORY_FIELDS: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "legacy": {
        "commercial": {LONG: ("commercial_positions_long_all", "comm_positions_long_all"),
                       SHORT: ("commercial_positions_short_all", "comm_positions_short_all")},
        "non_commercial": {LONG: ("noncommercial_positions_long_all", "noncomm_positions_long_all"),
                           SHORT: ("noncommercial_positions_short_all", "noncomm_positions_short_all"),
                           SPREAD: ("noncommercial_positions_spreading_all", "noncomm_positions_spread_all",
                                    "noncomm_positions_spreading_all")},
        "total_reportable": {LONG: ("total_reportable_positions_long_all", "tot_rept_positions_long_all"),
                             SHORT: ("total_reportable_positions_short_all", "tot_rept_positions_short",
                                     "tot_rept_positions_short_all")},
        "non_reportable": {LONG: ("nonreportable_positions_long_all", "nonrept_positions_long_all"),
                           SHORT: ("nonreportable_positions_short_all", "nonrept_positions_short_all")},
    },
    "tff": {
        "dealer": {LONG: ("dealer_positions_long_all",), SHORT: ("dealer_positions_short_all",),
                   SPREAD: ("dealer_positions_spread_all",)},
        "asset_manager": {LONG: ("asset_mgr_positions_long_all", "asset_mgr_positions_long"),
                          SHORT: ("asset_mgr_positions_short_all", "asset_mgr_positions_short"),
                          SPREAD: ("asset_mgr_positions_spread_all", "asset_mgr_positions_spread")},
        "leveraged_funds": {LONG: ("lev_money_positions_long_all", "lev_money_positions_long"),
                            SHORT: ("lev_money_positions_short_all", "lev_money_positions_short"),
                            SPREAD: ("lev_money_positions_spread_all", "lev_money_positions_spread")},
        "other_reportable": {LONG: ("other_rept_positions_long_all", "other_rept_positions_long"),
                             SHORT: ("other_rept_positions_short_all", "other_rept_positions_short"),
                             SPREAD: ("other_rept_positions_spread_all", "other_rept_positions_spread")},
        "total_reportable": {LONG: ("tot_rept_positions_long_all",),
                             SHORT: ("tot_rept_positions_short_all", "tot_rept_positions_short")},
        "non_reportable": {LONG: ("nonrept_positions_long_all",), SHORT: ("nonrept_positions_short_all",)},
    },
    "disaggregated": {
        "producer_merchant": {LONG: ("prod_merc_positions_long_all", "prod_merc_positions_long"),
                              SHORT: ("prod_merc_positions_short_all", "prod_merc_positions_short")},
        "swap_dealer": {LONG: ("swap_positions_long_all",),
                        SHORT: ("swap_positions_short_all",),
                        SPREAD: ("swap_positions_spread_all",)},
        "managed_money": {LONG: ("m_money_positions_long_all",), SHORT: ("m_money_positions_short_all",),
                          SPREAD: ("m_money_positions_spread_all", "m_money_positions_spread")},
        "other_reportable": {LONG: ("other_rept_positions_long_all", "other_rept_positions_long"),
                             SHORT: ("other_rept_positions_short_all", "other_rept_positions_short"),
                             SPREAD: ("other_rept_positions_spread_all", "other_rept_positions_spread")},
        "total_reportable": {LONG: ("tot_rept_positions_long_all",),
                             SHORT: ("tot_rept_positions_short_all",)},
        "non_reportable": {LONG: ("nonrept_positions_long_all",), SHORT: ("nonrept_positions_short_all",)},
    },
}

META_FIELDS: dict[str, tuple[str, ...]] = {
    "contract_name": ("market_and_exchange_names",),
    "contract_code": ("cftc_contract_market_code",),
    "report_date": ("as_of_date_in_form_yyyy_mm_dd", "report_date_as_yyyy_mm_dd"),
    "open_interest": ("open_interest_all",),
    "record_id": ("id",),
}

CATEGORY_LABELS = {
    "commercial": "Commercial", "non_commercial": "Non-commercial", "non_reportable": "Non-reportable",
    "total_reportable": "Total reportable", "dealer": "Dealer/Intermediary",
    "asset_manager": "Asset Manager/Institutional", "leveraged_funds": "Leveraged Funds",
    "other_reportable": "Other Reportables", "producer_merchant": "Producer/Merchant/Processor/User",
    "swap_dealer": "Swap Dealers", "managed_money": "Managed Money",
}

# categories that are "large traders" (reportable) per family; used for institutional vs non-reportable comparisons
REPORTABLE = {"legacy": ("commercial", "non_commercial"),
              "tff": ("dealer", "asset_manager", "leveraged_funds", "other_reportable"),
              "disaggregated": ("producer_merchant", "swap_dealer", "managed_money", "other_reportable")}

# meaningful within-family comparisons (never across families)
COMPARISONS = {
    "legacy": [("commercial", "non_commercial"), ("non_commercial", "non_reportable")],
    "tff": [("dealer", "leveraged_funds"), ("asset_manager", "leveraged_funds"),
            ("leveraged_funds", "non_reportable")],
    "disaggregated": [("managed_money", "producer_merchant"), ("managed_money", "swap_dealer"),
                      ("managed_money", "non_reportable")],
}

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def norm(name: str) -> str:
    s = _NON_ALNUM.sub("_", str(name).strip().lower()).strip("_")
    s = s.replace("postions", "positions")
    return re.sub(r"_+", "_", s)


def normalise_record(record: dict) -> dict[str, object]:
    """Return {normalised_name: value}; the first occurrence wins."""
    out: dict[str, object] = {}
    for k, v in record.items():
        out.setdefault(norm(k), v)
    return out


def lookup(nrec: dict[str, object], aliases: tuple[str, ...]) -> tuple[bool, object]:
    for a in aliases:
        if a in nrec:
            return True, nrec[a]
    return False, None


def expected_field_names(family: str) -> set[str]:
    names = {a[0] for a in META_FIELDS.values() if a[0] != "id"}
    for measures in CATEGORY_FIELDS[family].values():
        names.update(al[0] for al in measures.values())
    return names


def all_known_aliases(family: str) -> set[str]:
    names = {a for al in META_FIELDS.values() for a in al}
    for measures in CATEGORY_FIELDS[family].values():
        for al in measures.values():
            names.update(al)
    return names
