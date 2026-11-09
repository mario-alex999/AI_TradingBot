"""Evidence presented for review. Nothing here is invented: every item comes from a module's stored output, and anything
missing is shown as missing.

Each item has a ``kind``:
- ``confirmed``          a deterministic detection or measurement from a module (with its source when known);
- ``ai_interpretation``  text written by an AI model (Module 05's reviewer); never treated as fact;
- ``historical``         past statistics or context;
- ``contradictory``      evidence against the trade direction;
- ``risk_warning``       a Module 06 warning;
- ``missing``            the module could not supply it.

The snapshot is hashed; a decision records the hash of the snapshot the person saw.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from aurum.risk.repository import jsonable, stable_hash

# The technical concepts the specification asks the reviewer to see. Matched against the cited Module 03 findings only.
TECHNICAL_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Market structure", ("structure", "swing", "bias")),
    ("Higher highs / lower lows", ("higher high", "lower low", "hh", "ll")),
    ("Break of Structure (BOS)", ("bos", "break of structure")),
    ("Change of Character (CHoCH)", ("choch", "change of character")),
    ("Order blocks", ("order block", "order_block", "ob-")),
    ("Fair Value Gaps", ("fvg", "fair value")),
    ("Liquidity pools", ("liquidity pool", "lq-", "pool")),
    ("Liquidity sweeps", ("sweep",)),
    ("Premium / discount zones", ("premium", "discount")),
    ("Fibonacci retracement", ("fib",)),
    ("Optimal Trade Entry (OTE)", ("ote", "optimal trade entry")),
    ("Support / resistance", ("support", "resistance", "s/r")),
    ("Displacement", ("displacement",)),
    ("Inducement", ("inducement",)),
)


def item(kind: str, label: str, value: Any = None, source: str | None = None, at: Any = None, **extra: Any) -> dict[str, Any]:
    out = {"kind": kind, "label": label, "value": value, "source": source, "as_of": at}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def missing(label: str, why: str, source: str) -> dict[str, Any]:
    return item("missing", label, None, source, reason=why)


def section(name: str, items: list[dict[str, Any]], source: str, unavailable_reason: str | None = None,
            as_of: Any = None) -> dict[str, Any]:
    real = [i for i in items if i["kind"] != "missing"]
    status = "missing" if not real else ("partial" if len(real) < len(items) else "available")
    if unavailable_reason and not real:
        status = "missing"
    return {"name": name, "source": source, "status": status, "reason": unavailable_reason, "as_of": as_of, "items": items}


def build(version: dict[str, Any], risk: dict[str, Any] | None, candidate: dict[str, Any] | None,
          ai_review: dict[str, Any] | None, gates: list[dict[str, Any]], market: dict[str, Any] | None,
          now: datetime) -> dict[str, Any]:
    """``version`` is the trade idea version (parameters as decimal strings); ``risk`` the Module 06 result."""
    direction = version["direction"]
    sections = {
        "trade": _trade(version, risk),
        "technical": _technical(version, market),
        "market": _market(market, risk),
        "institutional": _institutional(market),
        "ai": _ai(version, candidate, ai_review, gates, direction),
        "risk": _risk(risk),
    }
    content = jsonable({"idea_id": version["idea_id"], "idea_version": version["version"], "built_at": now,
                        "analysis_version": version["analysis_version"], "sections": sections})
    completeness = {k: v["status"] for k, v in sections.items()}
    return {"content": content, "hash": stable_hash(content), "completeness": completeness}


# ---- sections ---------------------------------------------------------------------------------------------------------------
def _trade(v: dict[str, Any], risk: dict[str, Any] | None) -> dict[str, Any]:
    src = "module_05 idea + module_06 sizing"
    items = [item("confirmed", "Symbol", v["symbol"], "trade idea"), item("confirmed", "Market", v["market"], "trade idea"),
             item("confirmed", "Direction", v["direction"], "trade idea"), item("confirmed", "Entry", v["entry"], "trade idea"),
             item("confirmed", "Entry range", [v["entry_low"], v["entry_high"]], "trade idea"),
             item("confirmed", "Stop-loss", v["stop_loss"], "trade idea"),
             item("confirmed", "Take-profit levels", v["take_profits"], "trade idea")]
    if risk is None:
        items += [missing(lbl, "Module 06 did not return an assessment.", "module_06")
                  for lbl in ("Position size", "Risk amount", "Risk %", "Potential reward", "Risk-to-reward after costs",
                              "Spread and execution assumptions")]
    else:
        at = risk.get("timestamp")
        calc = risk.get("calculation") or {}

        def num(label: str, key: str, unit: str | None = None) -> dict[str, Any]:
            val = risk.get(key)
            return item("confirmed", label, val, "module_06", at, unit=unit) if val is not None else \
                missing(label, f"Module 06 returned no value ({risk.get('decision')}).", "module_06")
        items += [num("Position size (lots)", "final_volume"), num("Risk amount", "monetary_risk", risk.get("account_currency")),
                  num("Risk %", "risk_pct", "%"), num("Potential reward", "potential_reward", risk.get("account_currency")),
                  num("Risk-to-reward after costs", "risk_reward")]
        if calc:
            items.append(item("confirmed", "Spread and execution assumptions",
                              {k: calc.get(k) for k in ("spread_cost_per_lot", "slippage_cost_per_lot", "commission_per_lot",
                                                        "spread_points", "slippage_points") if k in calc} or calc,
                              "module_06", at))
        else:
            items.append(missing("Spread and execution assumptions", "Module 06 could not size the trade.", "module_06"))
    return section("Trade", items, src)


def _concept_hits(findings: list[dict[str, Any]], words: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for f in findings:
        text = " ".join(str(f.get(k) or "") for k in ("kind", "finding_id", "description")).lower()
        if any(w in text for w in words):
            out.append(f)
    return out


def _technical(v: dict[str, Any], market: dict[str, Any] | None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for ev in v.get("evidence") or []:
        for s in ev.get("sources") or []:
            if s.get("module") == "module_03":
                findings.append({**s, "description": ev.get("description")})
    items = []
    for label, words in TECHNICAL_CONCEPTS:
        hits = _concept_hits(findings, words)
        if hits:
            items.append(item("confirmed", label, [h.get("kind") or h.get("description") for h in hits], "module_03",
                              hits[0].get("confirmation_time") or hits[0].get("source_time"),
                              finding_ids=[h.get("finding_id") for h in hits if h.get("finding_id")]))
        else:
            items.append(missing(label, "Not reported for this idea by Module 03.", "module_03"))
    m03 = ((market or {}).get("integrations") or {}).get("module_03") or {}
    if m03.get("available"):
        items.insert(0, item("confirmed", "Higher-timeframe structure (Module 03 via Module 04)",
                             {k: m03.get(k) for k in m03 if k != "available"}, "module_03", (market or {}).get("as_of")))
    htf = v.get("higher_timeframe_context")
    if htf:
        items.insert(0, item("confirmed", "Higher-timeframe context (from the idea)", htf, "module_05"))
    return section("Technical analysis", items, "module_03",
                   None if findings or m03.get("available") else "No Module 03 findings were cited by this idea.")


def _market(market: dict[str, Any] | None, risk: dict[str, Any] | None) -> dict[str, Any]:
    if market is None:
        items = [missing(lbl, "Module 04 analysis is unavailable.", "module_04")
                 for lbl in ("Trading session", "Economic calendar", "Relevant news", "Volatility", "Market alerts")]
    else:
        at = market.get("as_of")
        sess, risk_ev, vol, news = market.get("sessions"), market.get("event_risk"), market.get("volatility"), market.get("news")
        items = [
            item("confirmed", "Trading session", sess, "module_04", at) if sess else missing("Trading session", "Not supplied.", "module_04"),
            item("confirmed", "Economic calendar", risk_ev, "module_04", at) if risk_ev else
            missing("Economic calendar", "Not supplied.", "module_04"),
            item("confirmed", "Relevant news", news, "module_04", at) if news else missing("Relevant news", "No news context.", "module_04"),
            item("confirmed", "Volatility", vol, "module_04", at) if vol else missing("Volatility", "Not supplied.", "module_04"),
        ]
        cond, issues = market.get("conditions"), market.get("issues")
        items.append(item("confirmed", "Market alerts", {"conditions": cond, "data_issues": issues}, "module_04", at)
                     if cond or issues else missing("Market alerts", "None reported.", "module_04"))
    ms = (risk or {}).get("market_status") or {}
    if ms:
        items.append(item("confirmed", "Spread conditions",
                          {k: ms.get(k) for k in ("spread_points", "median_spread_points", "quote_age_minutes", "market_open")},
                          "module_06", (risk or {}).get("timestamp")))
    else:
        items.append(missing("Spread conditions", "Module 06 market status unavailable.", "module_06"))
    return section("Market intelligence", items, "module_04", None if market else "Module 04 analysis is unavailable.",
                   (market or {}).get("as_of"))


def _institutional(market: dict[str, Any] | None) -> dict[str, Any]:
    m02 = ((market or {}).get("integrations") or {}).get("module_02") or {}
    labels = ("Commercial positioning", "Large speculator positioning", "Small speculator positioning",
              "COT historical context", "Positioning divergence")
    if not m02.get("available"):
        reason = m02.get("reason") or "Module 02 context is unavailable."
        return section("Institutional (COT)", [missing(lbl, reason, "module_02") for lbl in labels], "module_02", reason)
    items = [item("historical", "COT summary (weekly, delayed by design)", {k: m02.get(k) for k in m02 if k != "available"},
                  "module_02", m02.get("report_date"))]
    items += [missing(lbl, "Only the summary is in this snapshot; the full breakdown is on the COT Analysis page.", "module_02")
              for lbl in labels]
    return section("Institutional (COT)", items, "module_02")


def _ai(v: dict[str, Any], candidate: dict[str, Any] | None, review: dict[str, Any] | None, gates: list[dict[str, Any]],
        direction: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for ev in v.get("evidence") or []:
        items.append(item("confirmed", f"Scoring: {ev.get('component') or 'evidence'}", ev.get("description"), "module_05",
                          points=ev.get("points"), max_points=ev.get("max_points")))
    if not v.get("evidence"):
        items.append(missing("Supporting evidence", "The idea supplied no evidence items.", "module_05"))
    conf = v.get("ai_confidence")
    items.append(item("confirmed", "Confidence score", conf, "module_05",
                      explanation="Module 05's deterministic setup score (0-100) from the components above. It is not a "
                                  "probability of profit and does not affect the approval decision.")
                 if conf is not None else missing("Confidence score", "Not supplied.", "module_05"))
    if candidate is not None:
        items.append(item("confirmed", "Invalidation conditions", candidate.get("invalidation"), "module_05"))
    for g in gates or []:
        if g.get("result") in ("warn", "fail"):
            items.append(item("risk_warning" if g.get("result") == "warn" else "contradictory", f"Module 05 gate: {g.get('gate')}",
                              g.get("evidence"), "module_05", g.get("checked_at")))
    r = (review or {}).get("review") if review and review.get("status") == "reviewed" else None
    if r:
        at = (review or {}).get("created_at")
        items.append(item("ai_interpretation", "Trade thesis", r.get("summary"), "module_05 AI reviewer", at, model=review.get("model")))
        items.append(item("ai_interpretation", "Supporting points", r.get("supporting_points"), "module_05 AI reviewer", at))
        against = r.get("bearish_case") if direction == "BUY" else r.get("bullish_case")
        items.append(item("contradictory", "Case against this direction (AI)", against, "module_05 AI reviewer", at))
        items.append(item("ai_interpretation", "Identified weaknesses and risks", r.get("risks"), "module_05 AI reviewer", at))
        items.append(item("ai_interpretation", "Data gaps the AI noted", r.get("data_gaps"), "module_05 AI reviewer", at))
        items.append(item("ai_interpretation", "AI invalidation check", r.get("invalidation_check"), "module_05 AI reviewer", at))
        items.append(item("ai_interpretation", "AI verdict", {"verdict": r.get("verdict"),
                                                              "confidence_adjustment": r.get("confidence_adjustment")},
                          "module_05 AI reviewer", at))
    else:
        reason = (review or {}).get("reason") or "No AI review was run for this idea."
        items.append(missing("AI reasoning (thesis, counter-case, weaknesses)", reason, "module_05 AI reviewer"))
    if v.get("ai_summary") and not r:
        items.append(item("ai_interpretation", "Analysis summary", v["ai_summary"], "module_05"))
    items.append(item("confirmed", "Analysis version", v["analysis_version"], "module_05"))
    return section("AI analysis (Module 05)", items, "module_05")


def _risk(risk: dict[str, Any] | None) -> dict[str, Any]:
    if risk is None:
        return section("Risk (Module 06)", [missing("Risk assessment", "Module 06 did not return an assessment.", "module_06")],
                       "module_06", "Module 06 did not return an assessment.")
    at = risk.get("timestamp")
    rules = {r["rule_id"]: r for r in (risk.get("rule_violations") or []) + (risk.get("warnings") or []) + (risk.get("errors") or [])}
    passed = {r["rule_id"] for r in risk.get("passed_checks") or []}

    def rule(label: str, ids: tuple[str, ...]) -> dict[str, Any]:
        failed = [rules[i] for i in ids if i in rules]
        if failed:
            return item("risk_warning", label, [f"{f['rule_id']}: {f.get('message')}" for f in failed], "module_06", at)
        if all(i in passed for i in ids):
            return item("confirmed", label, "passed", "module_06", at, rules=list(ids))
        return missing(label, "Not evaluated (see Module 06 skipped checks).", "module_06")

    acct = ((risk.get("account_status") or {}).get("summary")) or {}
    port = ((risk.get("portfolio_status") or {}).get("summary")) or {}
    items = [
        item("confirmed", "Risk decision", risk.get("decision"), "module_06", at, assessment_id=risk.get("assessment_id")),
        item("confirmed", "Risk %", risk.get("risk_pct"), "module_06", at),
        item("confirmed", "Position size (lots)", risk.get("final_volume"), "module_06", at),
        rule("Stop-loss validation", ("R010", "R011", "R014")),
        rule("Take-profit validation", ("R012", "R016")),
        item("confirmed", "Account exposure", acct or None, "module_06", at) if acct else missing("Account exposure", "Unavailable.", "module_06"),
        item("confirmed", "Portfolio exposure", port or None, "module_06", at) if port else missing("Portfolio exposure", "Unavailable.", "module_06"),
        rule("Drawdown status", ("R033",)),
        rule("Daily loss status", ("R030",)),
        rule("Margin utilisation", ("R037",)),
        rule("Correlation risk", ("R044", "R045")),
    ]
    for w in risk.get("warnings") or []:
        items.append(item("risk_warning", f"{w['rule_id']} {w.get('name', '')}".strip(), w.get("message"), "module_06", at))
    for w in (risk.get("rule_violations") or []) + (risk.get("errors") or []):
        items.append(item("contradictory", f"{w['rule_id']} {w.get('name', '')}".strip(), w.get("message"), "module_06", at,
                          blocking=True))
    items.append(item("confirmed", "Assessment time and configuration",
                      {"timestamp": at, "expires_at": risk.get("expires_at"), "config_version": risk.get("config_version")},
                      "module_06", at))
    return section("Risk (Module 06)", items, "module_06", as_of=at)
