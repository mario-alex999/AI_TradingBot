"""Transparent, rule-based headline tone (method ``lexicon_v1``).

This is a DERIVED classification of a headline's wording, computed by AURUM, never a summary and never presented as
news. Every label lists the exact terms that produced it, so a reviewer can check it. No machine-learning model and no
external service is used, so results are deterministic and reproducible.

* Central-bank items (``category = central_bank``): ``policy_tone`` - hawkish | dovish | neutral | unclear.
  "hawkish" = wording about raising rates / tightening; "dovish" = cutting / easing; "neutral" = holding unchanged.
* Other headlines: ``risk_tone`` - positive | negative | neutral | unclear, from market-wording lexicons.

Limits (stated in every result): headlines are short, negation and sarcasm are handled only for simple forms
("not", "no", "without"), and a tone is context, not a forecast or a trading signal.
"""
from __future__ import annotations

import re
from typing import Any

METHOD = "lexicon_v1"
NOTE = ("Rule-based tone of the headline wording (lexicon_v1), derived by AURUM - not a forecast, not a trading "
        "signal. The matched terms are listed.")

POLICY = {
    "hawkish": [r"\brais(e|es|ed|ing) (\w+ ){0,3}rates?\b", r"\brate (hike|increase)s?\b",
                r"\bhik(e|es|ed|ing)\b", r"\btighten(s|ed|ing)?\b", r"\bincreas(e|es|ed|ing) (\w+ ){0,3}rates?\b",
                r"\binflation (risks?|pressures?) (remain|persist|rise)", r"\bquantitative tightening\b"],
    "dovish": [r"\bcut(s|ting)? (\w+ ){0,3}rates?\b", r"\brate cuts?\b", r"\blower(s|ed|ing)? (\w+ ){0,3}rates?\b",
               r"\breduc(e|es|ed|ing) (\w+ ){0,3}rates?\b", r"\beas(e|es|ed|ing)\b",
               r"\bloosen(s|ed|ing)?\b", r"\bstimulus\b", r"\basset purchases?\b"],
    "neutral": [r"\b(maintain|maintains|maintained|keeps?|kept|holds?|held|leaves?|left)\b.*\b(rates?|unchanged)\b",
                r"\b(bank rate|rates?)\b.{0,40}\b(maintained|unchanged|held|kept)\b", r"\bunchanged\b",
                r"\bon hold\b"],
}
RISK = {
    "positive": [r"\bsurg(e|es|ed|ing)\b", r"\brall(y|ies|ied)\b", r"\bjump(s|ed)?\b", r"\bbeats?\b",
                 r"\bstrong(er)?\b", r"\brebound(s|ed)?\b", r"\bgains?\b", r"\brecord high\b", r"\boptimism\b",
                 r"\bimprov(e|es|ed|ing)\b", r"\bexpan(d|ds|ded|sion)\b"],
    "negative": [r"\bplung(e|es|ed)\b", r"\bslump(s|ed)?\b", r"\btumbl(e|es|ed)\b", r"\bmiss(es|ed)?\b",
                 r"\bweak(er|ens|ened)?\b", r"\bfall(s|ing)?\b|\bfell\b", r"\bcontract(s|ed|ion)\b",
                 r"\brecession\b", r"\bcrisis\b", r"\bdefault(s|ed)?\b", r"\bsell-?off\b", r"\bfears?\b",
                 r"\bturmoil\b", r"\bdowngrad(e|es|ed)\b"],
}
NEGATION = re.compile(r"\b(not|no|without|never)\s+(\w+\s+){0,2}$", re.IGNORECASE)
_C = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in {**POLICY, **RISK}.items()}


def _hits(text: str, label: str) -> list[str]:
    out = []
    for rx in _C[label]:
        for m in rx.finditer(text):
            if NEGATION.search(text[:m.start()]):
                continue                                    # "not raise rates" does not count as hawkish
            out.append(m.group(0))
    return out


def headline_tone(headline: str, category: str | None = None, description: str | None = None) -> dict[str, Any]:
    text = " ".join(x for x in (headline, description or "") if x)
    if category == "central_bank":
        hits = {k: _hits(text, k) for k in POLICY}
        if hits["neutral"] and not (hits["hawkish"] or hits["dovish"]):
            label = "neutral"
        elif bool(hits["hawkish"]) != bool(hits["dovish"]):
            label = "hawkish" if hits["hawkish"] else "dovish"
        else:
            label = "unclear"                               # no policy wording, or both directions
        terms = sorted({t.lower() for v in hits.values() for t in v})
        return {"kind": "policy_tone", "label": label, "terms": terms, "method": METHOD, "note": NOTE}
    pos, neg = _hits(text, "positive"), _hits(text, "negative")
    score = len(pos) - len(neg)
    label = "unclear" if not (pos or neg) else "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return {"kind": "risk_tone", "label": label, "score": score, "terms": sorted({t.lower() for t in pos + neg}),
            "method": METHOD, "note": NOTE}


def summarise(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of tone labels per currency (items must carry ``tone`` and ``currencies``)."""
    by: dict[str, dict[str, int]] = {}
    for it in items:
        tone = it.get("tone") or {}
        for ccy in it.get("currencies") or ["(none)"]:
            d = by.setdefault(ccy, {})
            key = f"{tone.get('kind', 'tone')}:{tone.get('label', 'unclear')}"
            d[key] = d.get(key, 0) + 1
    return {"by_currency": by, "items": len(items), "method": METHOD, "note": NOTE}
