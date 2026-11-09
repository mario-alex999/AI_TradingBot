"""High-impact news and its likely market effect (method ``news_impact_v1``).

For each important scheduled release this builds:

* **Expectation** - the consensus forecast against the previous figure.
* **Scenarios** - what an above-forecast, in-line or below-forecast figure conventionally means for the currency and,
  through it, for each affected instrument. The rule per indicator is explicit (``POLARITY``): for most data a higher
  figure supports the currency; for unemployment and jobless claims a higher figure weighs on it.
* **Measured history** - for every scenario and instrument, how price actually moved in the
  ``REACTION_MINUTES`` after past releases of the same indicator, from stored candles: number of releases, average
  move, and how often the move matched the rule. No history -> ``None``, never a guess.
* **Outcome** - once released: the surprise (actual minus consensus), the scenario that occurred, the implied direction
  per instrument and the move measured so far.

Headlines get the existing rule-based tone (``sentiment.headline_tone``); a hawkish/dovish central-bank headline is
mapped to the currency it concerns. Everything here is conditional context for a human, never a trade instruction:
the actual figure of a future release is unknown, and markets can and do react against the conventional rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from aurum.market_intelligence.sentiment import headline_tone

UTC = timezone.utc
METHOD = "news_impact_v1"
REACTION_MINUTES = 60
HISTORY_YEARS = 3
HISTORY_MAX_EVENTS = 36
REACTION_TIMEFRAMES = ("M5", "M15")

# +1: a higher figure (or rate) supports the currency; -1: a higher figure weighs on it; absent: no numeric rule.
POLARITY: dict[str, int] = {
    "core_cpi": 1, "cpi": 1, "core_pce": 1, "pce": 1, "ppi": 1, "inflation_expectations": 1,
    "nonfarm_payrolls": 1, "adp": 1, "wage_growth": 1, "employment_change": 1, "rate_decision": 1,
    "gdp": 1, "ism": 1, "pmi": 1, "retail_sales": 1, "industrial_production": 1, "consumer_confidence": 1,
    "housing_starts": 1, "building_permits": 1, "existing_home_sales": 1, "new_home_sales": 1,
    "unemployment_rate": -1, "jobless_claims": -1,
}
POLARITY_WHY = {
    1: "A higher figure than expected is conventionally supportive of the currency (stronger growth, firmer inflation "
       "or tighter policy expectations).",
    -1: "A higher figure than expected is conventionally negative for the currency (a weaker labour market).",
}
NO_RULE = ("No numeric rule for this event type (speeches, minutes, press conferences, auctions): the reaction depends "
           "on what is said. Watch the headline tone.")
DISCLAIMER = ("Conditional scenarios and measured history, not a forecast: the actual figure is unknown until "
              "release, and markets can react against the conventional rule (positioning, revisions, details).")

SCENARIOS = (("above", "Above forecast", 1), ("inline", "In line", 0), ("below", "Below forecast", -1))


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def instrument_direction(symbol: str, currency: str | None, currency_sign: int) -> tuple[str | None, str]:
    """Direction of ``symbol`` when ``currency`` strengthens (+1) or weakens (-1): up | down | mixed | None."""
    s, c = symbol.upper(), (currency or "").upper()
    if currency_sign == 0 or not c:
        return None, "No directional effect expected."
    if len(s) == 6 and s.isalpha():
        base, quote = s[:3], s[3:]
        strong = "stronger" if currency_sign > 0 else "weaker"
        if c == base:
            return ("up" if currency_sign > 0 else "down"), f"{c} is the base currency: a {strong} {c} moves {s} the same way."
        if c == quote:
            what = "gold" if base == "XAU" else "silver" if base == "XAG" else base
            return ("down" if currency_sign > 0 else "up"), f"{s} is priced in {c}: a {strong} {c} moves {what} the other way."
        return None, f"{c} is not part of {s}."
    return "mixed", (f"{s} has no fixed rule: strong data can lift it (growth) or weigh on it (higher rates). "
                     "See the measured history.")


def classify_surprise(actual: float, consensus: float) -> str:
    """above | inline | below. Equal to the published precision counts as in line."""
    diff = round(actual - consensus, 6)
    return "above" if diff > 0 else "below" if diff < 0 else "inline"


def _reaction(frame: pd.DataFrame, released: datetime, minutes: int, now: datetime) -> tuple[float | None, int]:
    """Percent move from the last close before the release to the last close within ``minutes`` after it."""
    if frame.empty:
        return None, 0
    t = pd.Timestamp(released)
    before = frame.loc[frame["close_t"] <= t]
    end = min(t + pd.Timedelta(minutes=minutes), pd.Timestamp(now))
    after = frame.loc[(frame["ts"] >= t) & (frame["close_t"] <= end)]
    if before.empty or after.empty:
        return None, 0
    pre, post = float(before["close"].iloc[-1]), float(after["close"].iloc[-1])
    measured = int((after["close_t"].iloc[-1] - t).total_seconds() // 60)
    return (round((post - pre) / pre * 100, 4) if pre else None), measured


class NewsImpact:
    def __init__(self, repo: Any, candles: Any, impact: Any, symbols: Callable[[], list[str]],
                 now: Callable[[], datetime]):
        self.repo = repo
        self.candles = candles
        self.impact = impact
        self.symbols = symbols
        self.now = now
        self._frames: dict[tuple[str, str], pd.DataFrame] = {}

    # ------------------------------------------------------------------ candles
    def _frame(self, symbol: str) -> pd.DataFrame:
        for tf in REACTION_TIMEFRAMES:
            key = (symbol, tf)
            if key not in self._frames:
                self._frames[key] = self.candles.frame(symbol, tf, self.now())
            if not self._frames[key].empty:
                return self._frames[key]
        return self._frames[(symbol, REACTION_TIMEFRAMES[-1])]

    # ------------------------------------------------------------------ history
    def _history(self, ev: dict[str, Any], instruments: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        """{scenario: {instrument: stats}} from past releases of the same indicator (same currency + subcategory,
        or same name when the event is unclassified)."""
        now = self.now()
        past, _ = self.repo.events(now - timedelta(days=365 * HISTORY_YEARS), ev["scheduled_at"],
                                   currencies=[ev["currency"]] if ev.get("currency") else None)
        same = [p for p in past if p["id"] != ev["id"] and _num(p.get("actual")) is not None and _num(p.get("consensus")) is not None
                and ((ev.get("subcategory") and p.get("subcategory") == ev["subcategory"])
                     or (not ev.get("subcategory") and p.get("name") == ev.get("name")))][-HISTORY_MAX_EVENTS:]
        moves: dict[str, dict[str, list[float]]] = {}
        for p in same:
            sc = classify_surprise(_num(p["actual"]), _num(p["consensus"]))  # type: ignore[arg-type]
            for sym in instruments:
                mv, measured = _reaction(self._frame(sym), p["scheduled_at"], REACTION_MINUTES, now)
                if mv is not None and measured >= REACTION_MINUTES // 2:
                    moves.setdefault(sc, {}).setdefault(sym, []).append(mv)
        polarity = POLARITY.get(ev.get("subcategory") or "")
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for sc, by_sym in moves.items():
            sign = {"above": 1, "below": -1}.get(sc, 0) * (polarity or 0)
            for sym, mvs in by_sym.items():
                expected, _ = instrument_direction(sym, ev.get("currency"), sign)
                followed = None
                if expected in ("up", "down"):
                    followed = round(sum((m > 0) if expected == "up" else (m < 0) for m in mvs) / len(mvs) * 100)
                out.setdefault(sc, {})[sym] = {
                    "samples": len(mvs), "avg_move_pct": round(sum(mvs) / len(mvs), 4),
                    "up_pct": round(sum(m > 0 for m in mvs) / len(mvs) * 100), "followed_rule_pct": followed,
                    "minutes": REACTION_MINUTES}
        return out

    # ------------------------------------------------------------------ one event
    def event(self, ev: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        enabled = set(self.symbols())
        instruments = [a["instrument"] for a in ev.get("affected_instruments") or [] if a["instrument"] in enabled]
        why = {a["instrument"]: a.get("explanation") for a in ev.get("affected_instruments") or []}
        polarity = POLARITY.get(ev.get("subcategory") or "")
        actual, consensus, previous = _num(ev.get("actual")), _num(ev.get("consensus")), _num(ev.get("previous"))
        released = ev["scheduled_at"] <= now and actual is not None

        expectation = None
        if consensus is not None and previous is not None:
            vs = "higher" if consensus > previous else "lower" if consensus < previous else "unchanged"
            expectation = {"vs_previous": vs, "text": f"The forecast ({ev.get('consensus')}) is {vs} than the previous figure "
                                                      f"({ev.get('previous')})." if vs != "unchanged" else
                                                      f"The forecast is unchanged from the previous figure ({ev.get('previous')})."}

        history = self._history(ev, instruments) if polarity is not None and instruments else {}
        scenarios = []
        if polarity is not None:
            for key, label, s in SCENARIOS:
                sign = s * polarity
                effect = "bullish" if sign > 0 else "bearish" if sign < 0 else "neutral"
                rows = []
                for sym in instruments:
                    d, explanation = instrument_direction(sym, ev.get("currency"), sign)
                    rows.append({"instrument": sym, "direction": d, "explanation": explanation if sign else
                                 "A figure in line with the forecast is usually priced in; details and revisions drive the move.",
                                 "history": history.get(key, {}).get(sym)})
                scenarios.append({"key": key, "label": label, "currency_effect": effect, "instruments": rows})

        outcome = None
        if released and consensus is not None and polarity is not None:
            sc = classify_surprise(actual, consensus)  # type: ignore[arg-type]
            sign = {"above": 1, "below": -1}.get(sc, 0) * polarity
            rows = []
            for sym in instruments:
                expected, _ = instrument_direction(sym, ev.get("currency"), sign)
                mv, measured = _reaction(self._frame(sym), ev["scheduled_at"], REACTION_MINUTES, now)
                followed = None if mv is None or expected not in ("up", "down") else ((mv > 0) if expected == "up" else (mv < 0))
                rows.append({"instrument": sym, "expected": expected, "move_pct": mv, "minutes_measured": measured,
                             "followed_rule": followed})
            surprise = round(actual - consensus, 6)  # type: ignore[operator]
            outcome = {"scenario": sc, "surprise": surprise,
                       "surprise_pct": round(surprise / abs(consensus) * 100, 2) if consensus else None,
                       "currency_effect": "bullish" if sign > 0 else "bearish" if sign < 0 else "neutral",
                       "instruments": rows}
        elif released:
            outcome = {"scenario": None, "surprise": None, "surprise_pct": None, "currency_effect": None, "instruments": [],
                       "note": "Released, but without a numeric forecast or rule the surprise cannot be measured."}

        return {
            "id": ev["id"], "name": ev["name"], "currency": ev.get("currency"), "country": ev.get("country"),
            "category": ev.get("category"), "subcategory": ev.get("subcategory"), "importance": ev.get("importance"),
            "scheduled_at": ev["scheduled_at"], "time_precision": ev.get("time_precision"), "status": ev.get("status"),
            "released": released, "actual": ev.get("actual"), "consensus": ev.get("consensus"),
            "previous": ev.get("previous"), "unit": ev.get("unit"), "source_url": ev.get("source_url"),
            "instruments": [{"instrument": s, "why": why.get(s)} for s in instruments],
            "expectation": expectation, "polarity": polarity,
            "rule": POLARITY_WHY.get(polarity) if polarity is not None else NO_RULE,
            "scenarios": scenarios, "outcome": outcome,
            "history_samples": max((h["samples"] for by in history.values() for h in by.values()), default=0),
        }

    # ------------------------------------------------------------------ headlines
    def headlines(self, since: datetime, limit: int = 30) -> list[dict[str, Any]]:
        items, _ = self.repo.news(self.now(), None, 300, 0)
        enabled = set(self.symbols())
        out = []
        for it in items:
            pub = it.get("published_at")
            if pub is None or pub < since:
                continue
            tone = headline_tone(it["headline"], it.get("category"), it.get("description"))
            currencies = [c.upper() for c in it.get("currencies") or []]
            sign = {"hawkish": 1, "dovish": -1}.get(tone["label"], 0) if tone["kind"] == "policy_tone" else 0
            effect = None
            rows = []
            if sign and currencies:
                effect = {"currencies": currencies, "bias": "bullish" if sign > 0 else "bearish"}
                for c in currencies:
                    # Only instruments this currency drives (the same impact table the calendar uses).
                    linked = [m["instrument"] for m in self.impact.by_currency.get(c, []) if m["instrument"] in enabled]
                    for sym in linked:
                        d, explanation = instrument_direction(sym, c, sign)
                        if d is not None:
                            rows.append({"instrument": sym, "direction": d, "explanation": explanation})
            high = it.get("category") == "central_bank" or abs(tone.get("score") or 0) >= 2
            if not high:
                continue
            out.append({"id": it["id"], "headline": it["headline"], "source": it["source"], "url": it["url"],
                        "published_at": pub, "category": it.get("category"), "currencies": currencies,
                        "tone": {"kind": tone["kind"], "label": tone["label"], "terms": tone["terms"]},
                        "effect": effect, "instruments": rows})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ report
    def report(self, hours_back: int = 24, hours_ahead: int = 168, importance: list[str] | None = None,
               currencies: list[str] | None = None) -> dict[str, Any]:
        now = self.now()
        start, end = now - timedelta(hours=hours_back), now + timedelta(hours=hours_ahead)
        events, _ = self.repo.events(start, end, currencies=currencies, importance=importance or ["high"])
        built = [self.event(e) for e in events]
        upcoming = [e for e in built if not e["released"] and e["scheduled_at"] > now]
        return {
            "generated_at": now, "method": METHOD, "reaction_minutes": REACTION_MINUTES, "disclaimer": DISCLAIMER,
            "window": {"start": start, "end": end}, "importance": importance or ["high"],
            "next_event": upcoming[0] if upcoming else None,
            "events": built,
            "headlines": self.headlines(now - timedelta(hours=max(hours_back, 48))),
        }
