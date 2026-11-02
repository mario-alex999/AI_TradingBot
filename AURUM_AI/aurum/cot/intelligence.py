"""Institutional intelligence reports: metrics -> structured, transparent statements.

Every statement is tagged ``fact`` (straight from the CFTC report), ``metric`` (AURUM calculation, method in
docs/COT_METHODOLOGY.md) or ``interpretation`` (rule-based reading of the metrics). Nothing is inferred
for missing values; nothing is presented as a prediction. ``as_of`` reproduces what was knowable at that
UTC instant (reports released by then, candles closed by then).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from aurum.cot import divergence as dv
from aurum.cot.config import CotConfig, ContractMapping
from aurum.cot.fields import CATEGORY_LABELS, CATEGORY_FIELDS
from aurum.cot.positioning import STRENGTHS, compare_participants, consecutive_direction
from aurum.cot.release import release_info
from aurum.cot.repository import CotRepository

DISCLAIMER = ("COT data shows aggregate open futures positions by trader category as of the report date. It does not "
              "identify individual institutions, does not reveal trading profits, and is not a trading signal or a "
              "prediction. Non-reportable positions belong to traders below CFTC reporting thresholds and are not an "
              "exact measure of retail traders. Use as medium- to long-term positioning context only.")
STRENGTH_SCORE = {s: i - 3 for i, s in enumerate(STRENGTHS)}     # -3 .. +3
PriceLoader = Callable[[str, str], pd.DataFrame]


def _label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, cat)


def _fmt(v: float | None, pattern: str = "{:,.0f}") -> str:
    return "n/a" if v is None or (isinstance(v, float) and pd.isna(v)) else pattern.format(v)


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def freshness(release_ts: datetime, report_date, cfg: CotConfig, now: datetime) -> dict[str, Any]:
    nxt = release_info(report_date + timedelta(days=7), cfg.release)
    age_days = (now - release_ts).total_seconds() / 86400
    if age_days > cfg.analytics.stale_after_days:
        status = "stale"
    elif now >= nxt.release_timestamp + timedelta(hours=6):
        status = "update_due"
    else:
        status = "current"
    return {"status": status, "age_days": round(age_days, 1), "next_expected_report_date": nxt.report_date,
            "next_expected_release": nxt.release_timestamp}


def _interpret_category(c: dict[str, Any]) -> list[str]:
    name, net, strength = _label(c["category"]), c["net_position"], c["positioning_strength"]
    out = []
    if net is None:
        return out
    side = "net long" if net > 0 else "net short" if net < 0 else "flat"
    trend, n = c.get("trend_direction"), c.get("trend_reports") or 0
    if strength:
        rel = strength.replace("_", " ")
        p36 = c.get("historical_percentile_36m")
        out.append(f"{name} remain {side}; relative to the last 3 years their positioning is {rel}"
                   + (f" ({p36:.0f}th percentile)." if p36 is not None else "."))
    if trend and n >= 2:
        exposure = ("net-long exposure" if net > 0 else "net-short exposure")
        grows = (trend == "increasing") == (net > 0)
        out.append(f"Their {exposure} has {'increased' if grows else 'declined'} for {n} consecutive reports.")
        if net > 0 and not grows:
            out.append("This indicates a reduction in bullish exposure rather than a confirmed bearish reversal.")
        elif net < 0 and not grows:
            out.append("This indicates a reduction in bearish exposure rather than a confirmed bullish reversal.")
    ct = c.get("change_type") or ""
    if ct.startswith("reversal") or ct.startswith("new_net"):
        out.append(f"{name} flipped to {'net long' if 'long' in ct else 'net short'} this week.")
    if c.get("significant_change"):
        out.append(f"The weekly change for {name} is statistically large compared with its own history.")
    if c.get("unusual_positioning"):
        out.append(f"{name} positioning is historically unusual (outside the 5th-95th percentile band of 3 years).")
    return out


def family_report(repo: CotRepository, cfg: CotConfig, m: ContractMapping, mapping_id: int,
                  price_loader: PriceLoader | None, as_of: datetime | None, now: datetime) -> dict[str, Any]:
    ds = cfg.datasets[m.report_type]
    base = {"report_type": m.report_type, "dataset_name": ds.name, "dataset_id": ds.dataset_id,
            "dataset_id_status": ds.dataset_id_status,
            "contract": {"name": m.cftc_contract_name, "code": m.cftc_contract_code, "exchange": m.exchange,
                         "description": m.contract_description, "mapping_status": m.mapping_status,
                         "verification_source": m.verification_source, "verified_at": m.verified_at},
            "primary_category": m.primary_category}
    met = repo.metrics_frame(mapping_id=mapping_id, as_of=as_of)
    if met.empty:
        return {**base, "available": False, "reason": "No COT metrics stored (run: python -m aurum.cli cot backfill)."}
    met = met.sort_values(["report_date", "trader_category"])
    latest_date = met["report_date"].max()
    latest_rows = met[met["report_date"] == latest_date]
    reps = repo.report_frame(mapping_id=mapping_id, as_of=as_of)
    rep = reps[reps["report_date"] == latest_date].iloc[-1]
    pos = repo.positions_frame(mapping_id, as_of=as_of)
    pos_latest = pos[pos["report_date"] == latest_date].set_index("trader_category")
    cats = []
    latest_by_cat: dict[str, dict[str, Any]] = {}
    for cat in CATEGORY_FIELDS[m.report_type]:
        row = latest_rows[latest_rows["trader_category"] == cat]
        if row.empty:
            continue
        r = {k: _clean(v) for k, v in row.iloc[0].to_dict().items()}
        hist = met[met["trader_category"] == cat]
        direction_, nrep = consecutive_direction([_clean(v) for v in hist["weekly_net_change"]])
        p = pos_latest.loc[cat] if cat in pos_latest.index else None
        entry = {
            "category": cat, "label": _label(cat),
            "long": _clean(p["long_positions"]) if p is not None else None,
            "short": _clean(p["short_positions"]) if p is not None else None,
            "spreading": _clean(p["spreading_positions"]) if p is not None else None,
            **{k: r.get(k) for k in ("net_position", "weekly_net_change", "long_change", "short_change",
                                     "net_position_pct_oi", "historical_percentile_12m", "historical_percentile_24m",
                                     "historical_percentile_36m", "historical_percentile_48m", "cot_index", "z_score",
                                     "positioning_direction", "positioning_strength", "classification_score",
                                     "change_type", "change_driver", "significant_change", "unusual_positioning",
                                     "history_weeks")},
            "trend_direction": direction_, "trend_reports": nrep,
        }
        cats.append(entry)
        latest_by_cat[cat] = entry
    rel_ts = _clean(rep["release_timestamp"])
    fresh = freshness(rel_ts, latest_date, cfg, as_of or now)
    comparisons = compare_participants(latest_by_cat, m.report_type)
    # divergence on the primary category
    div_out: dict[str, Any] = {"available": False, "reason": "Price loader not configured."}
    if price_loader is not None:
        candles = price_loader(m.aurum_symbol, cfg.analytics.divergence.price_timeframe)
        prim = met[met["trader_category"] == m.primary_category][["report_date", "net_position", "release_timestamp"]]
        res = dv.analyse(prim, candles, cfg.analytics.divergence, as_of=as_of)
        div_out = {"available": res.available, "reason": res.reason, "category": m.primary_category,
                   "window_reports": res.window_reports, "price_timeframe": cfg.analytics.divergence.price_timeframe,
                   "price_coverage": {"from": res.price_coverage[0], "to": res.price_coverage[1]},
                   "latest": res.latest, "recent_signals": res.signals[-5:]}
    # statements
    facts, metrics_s, interp = [], [], []
    oi = _clean(rep["open_interest"])
    facts.append(f"{m.cftc_contract_name} ({m.cftc_contract_code}) - {ds.name}: positions as of {latest_date}, "
                 f"publicly released {rep['release_date']} ({_clean(rep['release_method'])}); "
                 f"open interest {_fmt(oi)} contracts.")
    for c in cats:
        if c["category"] == "total_reportable":
            continue
        if c["long"] is not None:
            facts.append(f"{c['label']}: {_fmt(c['long'])} long, {_fmt(c['short'])} short"
                         + (f", {_fmt(c['spreading'])} spreading" if c["spreading"] is not None else "")
                         + f" (net {_fmt(c['net_position'], '{:+,.0f}')}).")
        metrics_s.append(f"{c['label']}: weekly net change {_fmt(c['weekly_net_change'], '{:+,.0f}')}, "
                         f"net {_fmt(c['net_position_pct_oi'], '{:+.1f}')}% of OI, 36m percentile "
                         f"{_fmt(c['historical_percentile_36m'], '{:.0f}')}, COT index {_fmt(c['cot_index'], '{:.0f}')}, "
                         f"z-score {_fmt(c['z_score'], '{:+.2f}')}.")
    prim = latest_by_cat.get(m.primary_category)
    if prim:
        interp += _interpret_category(prim)
    for cmp in comparisons:
        if cmp["positioning_relation"] == "opposed":
            interp.append(f"{cmp['label']}: the groups hold opposite net positions "
                          f"({_fmt(cmp['group_a_net'], '{:+,.0f}')} vs {_fmt(cmp['group_b_net'], '{:+,.0f}')}).")
        if cmp["flow_relation"] == "diverging_flows":
            interp.append(f"{cmp['label']}: this week's flows moved in opposite directions.")
    if div_out.get("available") and div_out.get("latest") and div_out["latest"]["signals"]:
        L = div_out["latest"]
        for sig in L["signals"]:
            interp.append(f"Price vs {_label(m.primary_category)}: {dv.SIGNAL_TEXT[sig]} "
                          f"(reports {L['window_start_report_date']} -> {L['report_date']}, price change "
                          f"{L['price_change_pct']:+.2f}%, net change {L['net_position_change']:+,}). "
                          f"A divergence is not proof of a reversal.")
    if "non_reportable" in latest_by_cat:
        interp.append("Non-reportable traders are those below CFTC reporting thresholds - not an exact proxy for retail.")
    # confidence
    reasons = []
    hist_weeks = (prim or {}).get("history_weeks") or 0
    issues, _ = repo.issues(symbol=m.aurum_symbol, limit=200)
    fam_issues = [i for i in issues if i["report_type"] in (m.report_type, None)]
    if hist_weeks < 156:
        reasons.append(f"only {hist_weeks} weeks of history in the 48-month window")
    if fresh["status"] != "current":
        reasons.append(f"data freshness: {fresh['status']}")
    if bool(rep["release_uncertain"]):
        reasons.append("release date uncertain")
    if any(i["severity"] in ("error", "critical") for i in fam_issues):
        reasons.append("open data-quality errors")
    if m.mapping_status != "verified":
        reasons.append("contract mapping not verified")
    level = "high" if not reasons else "low" if len(reasons) >= 2 or fresh["status"] == "stale" else "medium"
    return {**base, "available": True, "report_date": latest_date, "release_date": _clean(rep["release_date"]),
            "release_timestamp": rel_ts, "release_method": _clean(rep["release_method"]),
            "release_uncertain": bool(rep["release_uncertain"]), "report_id": int(rep["id"]),
            "open_interest": oi, "freshness": fresh, "categories": cats, "comparisons": comparisons,
            "divergence": div_out,
            "statements": {"facts": facts, "metrics": metrics_s, "interpretation": interp},
            "confidence": {"level": level, "reasons": reasons},
            "data_quality_warnings": [{"severity": i["severity"], "issue_type": i["issue_type"],
                                       "report_date": i["report_date"], "description": i["description"]}
                                      for i in fam_issues if i["severity"] != "info"][:20]}


def instrument_report(repo: CotRepository, cfg: CotConfig, symbol: str, mapping_ids: dict[tuple[str, str], int],
                      price_loader: PriceLoader | None = None, as_of: datetime | None = None) -> dict[str, Any]:
    symbol = symbol.upper()
    maps = cfg.mappings_for(symbol)
    if not maps:
        raise KeyError(f"No COT mapping configured for {symbol}")
    now = datetime.now(timezone.utc)
    families = [family_report(repo, cfg, m, mapping_ids[(m.aurum_symbol, m.report_type)], price_loader, as_of, now)
                for m in maps if (m.aurum_symbol, m.report_type) in mapping_ids]
    scores = []
    for f in families:
        if not f.get("available"):
            continue
        prim = next((c for c in f["categories"] if c["category"] == f["primary_category"]), None)
        if prim and prim["positioning_strength"]:
            scores.append((f["report_type"], STRENGTH_SCORE[prim["positioning_strength"]], prim))
    lean = None
    if not scores:
        stance, text = "unavailable", f"No stored COT positioning for {symbol}."
    else:
        # bullish/bearish only for strong (|score| >= 2 on the -3..+3 scale) agreement; moderate readings -> neutral
        # with a "lean"; opposite non-neutral readings across families -> mixed
        vals = [s for _, s, _ in scores]
        avg = sum(vals) / len(vals)
        if any(v >= 1 for v in vals) and any(v <= -1 for v in vals):
            stance = "mixed"
        else:
            stance = "bullish" if avg >= 2 else "bearish" if avg <= -2 else "neutral"
            lean = "bullish" if 0 < avg < 2 else "bearish" if -2 < avg < 0 else None
        parts = [f"{_label(p['category'])} ({rt}) {p['positioning_strength'].replace('_', ' ')}" for rt, _, p in scores]
        text = (f"Overall institutional positioning context for {symbol}: {stance}"
                + (f", leaning {lean}" if lean else "") + ". Based on " + "; ".join(parts)
                + ". This is relative-to-history positioning context, not a forecast or entry signal.")
    conf = [f["confidence"]["level"] for f in families if f.get("available")]
    overall_conf = "low" if "low" in conf or not conf else "medium" if "medium" in conf else "high"
    return {"instrument": symbol, "generated_at": now, "as_of": as_of, "families": families,
            "overall": {"stance": stance, "lean": lean, "interpretation": text, "confidence": overall_conf},
            "disclaimer": DISCLAIMER}
