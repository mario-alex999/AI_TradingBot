"""Module 04 service: orchestrates sessions, levels, liquidity, statistics, volatility, calendar, macro, news,
conditions and scores. Read-only with respect to markets: it never connects to MT5 and never places orders.

Price-derived parts (sessions, levels, liquidity, statistics, volatility) depend only on Module 01's stored candles
and keep working when every external provider is down. Provider outages are reported, never papered over.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.engine import Engine

from aurum.config import InstrumentConfig, Settings, get_settings, load_instruments
from aurum.market_intelligence.sentiment import headline_tone, summarise as summarise_tone
from aurum.market_intelligence import conditions as cond_mod
from aurum.market_intelligence import scoring
from aurum.market_intelligence.candles import CandleSource, RepositoryCandleSource
from aurum.market_intelligence.config import MODULE_VERSION, MIConfig, load_mi_config
from aurum.market_intelligence.economic_calendar import (EventClassifier, ImpactMapper, IMPORTANCE,
                                                          instrument_event_risk)
from aurum.market_intelligence.http import HttpClient, ProviderError
from aurum.market_intelligence.levels import all_levels
from aurum.market_intelligence.liquidity import session_liquidity
from aurum.market_intelligence.macro import (RateSources, SeriesSources, decisions as derive_decisions,
                                             fred_known_from, known_from, next_meeting)
from aurum.market_intelligence.module_adapters import Module02Adapter, Module03Adapter
from aurum.market_intelligence.news import FinnhubNewsProvider, OfficialFeedProvider
from aurum.market_intelligence.providers import FULL_CALENDAR_PROVIDERS, build_providers, provider_registry
from aurum.market_intelligence.repository import MiRepository
from aurum.market_intelligence.sessions import session_history, sessions_snapshot
from aurum.market_intelligence.statistics import session_statistics
from aurum.market_intelligence.volatility import event_window_range, volatility_report

log = logging.getLogger(__name__)
UTC = timezone.utc
DISCLAIMER = ("Market-intelligence context for decision support. It is not a trade signal, does not place or approve "
              "orders, and does not predict price direction; a human operator decides.")


class UnknownInstrumentError(KeyError):
    pass


class SchemaNotReady(RuntimeError):
    pass


def _utc(t: datetime | None) -> datetime:
    if t is None:
        return datetime.now(UTC).replace(microsecond=0)
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


class MarketIntelligenceService:
    def __init__(self, settings: Settings | None = None, engine: Engine | None = None, cfg: MIConfig | None = None,
                 candles: CandleSource | None = None, module03: Any = None, module02: Any = None,
                 http_factory: Callable[[list[str]], HttpClient] | None = None,
                 instruments: dict[str, InstrumentConfig] | None = None, now: Callable[[], datetime] | None = None):
        from aurum.database.connection import engine_from_settings
        from aurum.database.repository import Repository
        self.settings = settings or get_settings()
        self.engine = engine or engine_from_settings(self.settings)
        self.cfg = cfg or load_mi_config()
        self.candles = candles or RepositoryCandleSource(Repository(self.engine))
        self.m03 = module03 if module03 is not None else Module03Adapter()
        self.m02 = module02 if module02 is not None else Module02Adapter(self.engine)
        self.repo = MiRepository(self.engine)
        self.instruments = instruments or load_instruments(self.settings.resolve(self.settings.instruments_config))
        self._http_factory = http_factory
        self.now = now or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.classifier = EventClassifier(self.cfg)
        self.impact = ImpactMapper(self.cfg)
        self.analysis_ttl = float(self.cfg.section("performance").get("analysis_cache_seconds", 60))
        self._analysis_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}

    def clear_caches(self) -> None:
        """Called after every refresh so new calendar, macro or news data is visible immediately."""
        self._analysis_cache.clear()
        for c in (self.m03, self.candles):
            if hasattr(c, "clear"):
                c.clear()

    # ------------------------------------------------------------------------------------------ helpers
    def http(self, secrets: list[str] | None = None) -> HttpClient:
        return self._http_factory(secrets or []) if self._http_factory else HttpClient(secrets=secrets)

    def instrument(self, symbol: str) -> InstrumentConfig:
        s = (symbol or "").upper()
        if s not in self.instruments:
            raise UnknownInstrumentError(f"Unknown instrument '{symbol}'. Configured: {', '.join(self.instruments)}")
        return self.instruments[s]

    def symbols(self) -> list[str]:
        enabled = [s for s in self.settings.enabled_instruments if s in self.instruments]
        return enabled or list(self.instruments)

    def _require_schema(self) -> None:
        if not self.repo.schema_ready():
            raise SchemaNotReady("Module 04 tables are not initialised: run `python -m aurum.cli init-db`.")

    # ------------------------------------------------------------------------------------------ sessions
    def sessions(self, as_of: datetime | None = None, symbol: str | None = None) -> dict[str, Any]:
        t = _utc(as_of)
        hours = self.instrument(symbol).trading_hours if symbol else None
        snap = sessions_snapshot(self.cfg, t, symbol.upper() if symbol else None, hours)
        snap["config_fingerprint"] = self.cfg.fingerprint
        return snap

    def session_history(self, start: datetime, end: datetime, names: list[str] | None = None,
                        include_kill_zones: bool = False) -> list[dict[str, Any]]:
        if end <= start:
            raise ValueError("end must be after start.")
        if end - start > timedelta(days=120):
            raise ValueError("History window is limited to 120 days.")
        return [i.to_dict() for i in session_history(self.cfg, start, end, names, include_kill_zones)]

    def levels(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        inst, t = self.instrument(symbol), _utc(as_of)
        return all_levels(self.cfg, self.candles, inst.symbol, t, inst.trading_hours)

    def module03_context(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        tf = str(self.cfg.section("liquidity").get("module03_timeframe") or "M15")
        ctx = self.m03.context(symbol, tf, as_of)
        if not ctx.get("available") and tf != "H1":
            ctx2 = self.m03.context(symbol, "H1", as_of)
            if ctx2.get("available"):
                return ctx2
        return ctx

    def liquidity(self, symbol: str, as_of: datetime | None = None, levels: dict[str, Any] | None = None,
                  m03: dict[str, Any] | None = None) -> dict[str, Any]:
        inst, t = self.instrument(symbol), _utc(as_of)
        lv = levels or self.levels(inst.symbol, t)
        ctx = m03 if m03 is not None else self.module03_context(inst.symbol, t)
        return session_liquidity(self.cfg, self.candles, inst.symbol, t, lv["levels"], ctx, inst.trading_hours)

    def statistics(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        inst, t = self.instrument(symbol), _utc(as_of)
        return session_statistics(self.cfg, self.candles, inst.symbol, t, inst.trading_hours)

    def volatility(self, symbol: str, as_of: datetime | None = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
        inst, t = self.instrument(symbol), _utc(as_of)
        st = stats if stats is not None else self.statistics(inst.symbol, t)
        return volatility_report(self.cfg, self.candles, inst.symbol, t, inst.trading_hours, session_stats=st)

    # ------------------------------------------------------------------------------------------ calendar
    def calendar_coverage(self, as_of: datetime | None = None) -> dict[str, Any]:
        """Coverage levels: ``full`` (a fresh full third-party calendar), ``official`` (fresh official calendars for
        the listed currencies; explicit gaps), ``partial`` (some official data), ``none``. Only provider successes at
        or before ``as_of`` count, so a historical analysis never relies on a later download."""
        t = _utc(as_of)
        stale_h = float(self.cfg.section("event_risk").get("stale_after_hours", 36))
        oc = self.cfg.section("official_calendars")
        off_stale_h = float(oc.get("stale_after_hours", 168))
        sources = {s["provider"]: s for s in self.repo.sources()} if self.repo.schema_ready() else {}

        def fresh(p: str, hours: float) -> bool:
            s = sources.get(p) or {}
            ok = s.get("last_success_at")
            return bool(ok) and _utc(ok) <= t + timedelta(minutes=1) and t - _utc(ok) <= timedelta(hours=hours)

        full = [p for p in FULL_CALENDAR_PROVIDERS if p in sources]
        fresh_full = [p for p in full if fresh(p, stale_h)]
        partial = [p for p, s in sources.items() if p not in FULL_CALENDAR_PROVIDERS and s.get("last_success_at")
                   and _utc(s["last_success_at"]) <= t + timedelta(minutes=1)]
        schedule_ok = fresh("official_schedule", off_stale_h)
        currencies = {}
        for ccy, provs in (oc.get("coverage") or {}).items():
            missing = [p for p in provs if not fresh(p, off_stale_h)] + ([] if schedule_ok else ["official_schedule"])
            currencies[str(ccy)] = {"covered": not missing, "providers": list(provs), "missing": missing}
        gaps = {k: list(v) for k, v in (oc.get("gaps") or {}).items()}
        if fresh_full:
            return {"complete": True, "level": "full", "providers": fresh_full + partial, "currencies": currencies,
                    "gaps": {}, "reason": f"Full calendar from {', '.join(fresh_full)}."}
        covered = sorted(c for c, v in currencies.items() if v["covered"])
        if full:
            why = "stale" if any(sources[p].get("last_success_at") for p in full) else "failing"
            prefix = f"Full calendar provider {', '.join(full)} is {why}; "
        else:
            prefix = "No full economic-calendar provider is configured; "
        if covered:
            return {"complete": len(covered) == len(currencies), "level": "official", "providers": partial,
                    "currencies": currencies, "gaps": gaps,
                    "reason": prefix + "official agency and central-bank calendars cover " + ", ".join(covered)
                    + " (schedules only - no consensus values; private surveys are not covered)."}
        reason = prefix + ("only " + ", ".join(partial) + " data is available." if partial
                           else "no calendar data is available.")
        return {"complete": False, "level": "partial" if partial else "none", "providers": partial,
                "currencies": currencies, "gaps": gaps, "reason": reason}

    def symbol_currencies(self, symbol: str) -> list[str]:
        """Currencies whose releases affect ``symbol`` directly or as a macro driver (impact.currencies)."""
        cur = (self.cfg.section("impact").get("currencies") or {})
        return sorted(str(c) for c, rows in cur.items() if any(r.get("instrument") == symbol for r in rows or []))

    def symbol_coverage(self, cov: dict[str, Any], symbol: str) -> dict[str, Any]:
        """Coverage as it applies to one instrument: official coverage is complete when every currency that drives
        the instrument is covered."""
        if cov.get("level") != "official":
            return cov
        need = self.symbol_currencies(symbol)
        per = cov.get("currencies") or {}
        missing = [c for c in need if not (per.get(c) or {}).get("covered")]
        gaps = {c: (cov.get("gaps") or {}).get(c, []) for c in need}
        gaps["other"] = (cov.get("gaps") or {}).get("other", [])
        return {**cov, "complete": bool(need) and not missing, "symbol_currencies": need,
                "uncovered_currencies": missing, "gaps": gaps,
                "reason": (f"Official calendars cover {', '.join(need)} for {symbol} (schedules only; gaps listed)."
                           if need and not missing else
                           f"Official calendars do not cover {', '.join(missing) or 'this instrument'} for {symbol}.")}

    def refresh_calendar(self, days_back: int = 7, days_ahead: int = 21,
                         progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        self._require_schema()
        say = progress or (lambda m: None)
        start = self.now().date() - timedelta(days=days_back)
        end = self.now().date() + timedelta(days=days_ahead)
        run_uid = str(uuid.uuid4())
        results = []
        http = None
        try:
            secrets = [x.get_secret_value() for x in (self.settings.economic_calendar_api_key,
                                                      self.settings.fred_api_key) if x is not None]
            http = self.http(secrets)
        except Exception:  # noqa: BLE001
            http = None
        symbols = self.symbols()
        for p in build_providers(self.settings, self.cfg, http):
            started = self.now()
            say(f"{p.name}: fetching {start} .. {end}")
            try:
                events = p.fetch(start, end)
            except ProviderError as exc:
                msg = str(exc)
                self.repo.update_source(p.name, full_calendar=p.full_calendar, requires_key=not (p.name == "official_schedule" or getattr(p, "official", False)),
                                        status="error", last_attempt_at=started, last_error=msg[:500])
                self.repo.log_ingestion(run_uid=run_uid, provider=p.name, started_at=started, finished_at=self.now(),
                                        status="failed", window_start=start, window_end=end, error=msg[:1000],
                                        details={"kind": exc.kind, "status": exc.status})
                results.append({"provider": p.name, "status": "failed", "error": msg, "kind": exc.kind})
                say(f"{p.name}: FAILED - {msg}")
                continue
            valid, invalid, seen = [], 0, set()
            for ev in events:
                if not ev.name or ev.scheduled_at is None or ev.scheduled_at.tzinfo is None:
                    invalid += 1
                    continue
                if ev.importance is not None and ev.importance not in IMPORTANCE:
                    ev.importance = None
                if ev.key() in seen:                  # duplicate inside one provider response
                    invalid += 1
                    continue
                seen.add(ev.key())
                self.classifier.classify(ev)
                ev.affected_instruments = self.impact.affected(ev, symbols)
                valid.append(ev)
            stats = self.repo.upsert_events(valid, run_uid)
            self.repo.update_source(p.name, full_calendar=p.full_calendar, requires_key=not (p.name == "official_schedule" or getattr(p, "official", False)),
                                    status="ok", last_attempt_at=started, last_success_at=self.now(), last_error=None,
                                    last_event_count=len(valid), window_start=start, window_end=end)
            self.repo.log_ingestion(run_uid=run_uid, provider=p.name, started_at=started, finished_at=self.now(),
                                    status="success", window_start=start, window_end=end, received=len(events),
                                    inserted=stats["inserted"], updated=stats["updated"], unchanged=stats["unchanged"],
                                    invalid=invalid)
            results.append({"provider": p.name, "status": "success", "received": len(events), "invalid": invalid,
                            **stats})
            say(f"{p.name}: {len(events)} received, {stats['inserted']} new, {stats['updated']} updated, "
                f"{stats['unchanged']} unchanged, {invalid} invalid")
        self.repo.create_run(self.cfg, "calendar", None, self.now(), "ok" if all(r["status"] == "success" for r in results)
                             else "partial", {"providers": results, "window": [start, end]})
        self.clear_caches()
        return {"run_uid": run_uid, "window_start": start, "window_end": end, "providers": results,
                "coverage": self.calendar_coverage()}

    def calendar(self, start: datetime | None = None, end: datetime | None = None, as_of: datetime | None = None,
                 currencies: list[str] | None = None, importance: list[str] | None = None,
                 categories: list[str] | None = None, instrument: str | None = None, page: int = 1,
                 page_size: int = 50) -> dict[str, Any]:
        self._require_schema()
        now = self.now()
        hist = as_of is not None and _utc(as_of) < now - timedelta(minutes=1)
        items, total = self.repo.events(start, end, as_of=_utc(as_of) if hist else None, currencies=currencies,
                                        importance=importance, categories=categories,
                                        instrument=instrument.upper() if instrument else None,
                                        offset=(page - 1) * page_size, limit=page_size)
        ref = _utc(as_of) if as_of else now
        for e in items:
            e["minutes_until_release"] = round((e["scheduled_at"] - ref).total_seconds() / 60, 1)
            if e["status"] == "scheduled" and 0 < e["minutes_until_release"] <= 1440:
                e["status_display"] = "upcoming"
        return {"items": items, "page": page, "page_size": page_size, "total": total,
                "coverage": self.calendar_coverage(ref), "as_of": ref, "point_in_time": hist}

    def event_history_context(self, ev: dict[str, Any], symbol: str, as_of: datetime, n: int = 5) -> dict[str, Any]:
        """Range after past releases of the same event type (same provider name or subcategory + currency)."""
        minutes = int(self.cfg.section("event_risk").get("history_window_minutes", 60))
        past, _ = self.repo.events(as_of - timedelta(days=400), as_of - timedelta(minutes=minutes),
                                   as_of=as_of, currencies=[ev["currency"]] if ev.get("currency") else None)
        same = [p for p in past if p["name"] == ev["name"] or (ev.get("subcategory") and
                                                                 p.get("subcategory") == ev.get("subcategory"))]
        ratios = []
        for p in same[-n:]:
            r = event_window_range(self.candles, symbol, p["scheduled_at"], minutes, as_of)
            if r and r.get("ratio") is not None:
                ratios.append({**r, "event": p["name"]})
        return {"samples": len(ratios), "mean_ratio": sum(r["ratio"] for r in ratios) / len(ratios) if ratios else None,
                "observations": ratios, "window_minutes": minutes,
                "note": "Range after past releases / (mean bar range before release x sqrt(bars)); "
                        "1.0 = an ordinary move. History only - not a forecast."}

    def event_risk(self, symbol: str, as_of: datetime | None = None, volatility: dict[str, Any] | None = None) -> dict[str, Any]:
        inst, t = self.instrument(symbol), _utc(as_of)
        if not self.repo.schema_ready():
            return {"symbol": inst.symbol, "as_of": t, "state": "UNKNOWN",
                    "reason": "Module 04 tables are not initialised.", "active_events": [], "upcoming_events": [],
                    "next_event": None, "coverage": {"complete": False, "level": "none"}}
        hist = t < self.now() - timedelta(minutes=1)
        events, _ = self.repo.events(t - timedelta(hours=3), t + timedelta(days=2), as_of=t if hist else None,
                                     instrument=inst.symbol)
        cov = self.symbol_coverage(self.calendar_coverage(t), inst.symbol)
        risk = instrument_event_risk(self.cfg, inst.symbol, events, t, cov)
        focus = (risk["active_events"] or [None])[0] or risk.get("next_event")
        risk["historical_context"] = self.event_history_context(focus, inst.symbol, t) if focus else None
        return risk

    # ------------------------------------------------------------------------------------------ macro
    def refresh_macro(self, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        self._require_schema()
        say = progress or (lambda m: None)
        if not self.settings.official_data_enabled:
            return {"status": "disabled", "reason": "OFFICIAL_DATA_ENABLED=false"}
        banks = self.cfg.banks.get("banks") or {}
        start = date.fromisoformat(str(self.cfg.section("macro").get("rate_history_start") or "2024-01-01"))
        fred = self.settings.fred_api_key.get_secret_value() if self.settings.fred_api_key else None
        http = self.http([fred] if fred else [])
        src = RateSources(http)
        now = self.now()
        results: dict[str, Any] = {"rates": {}, "series": {}}
        for bank, b in banks.items():
            if not b.get("rate_source"):
                results["rates"][bank] = {"status": "not_sourced", "reason": b.get("note")}
                continue
            try:
                obs = src.fetch(b["rate_source"], bank, start)
            except (ProviderError, ValueError, KeyError) as exc:
                results["rates"][bank] = {"status": "failed", "error": str(exc)[:300]}
                self.repo.record_issues(None, None, "macro", [{"issue": "rate_source_failed", "provider": bank,
                                                               "severity": "warning", "message": str(exc)[:300]}])
                say(f"{bank}: FAILED - {exc}")
                continue
            rows = []
            for o in obs:
                kf, _ = known_from(b, o.effective_date)
                rows.append({"bank": bank, "effective_date": o.effective_date, "rate": o.rate,
                             "rate_lower": o.rate_lower, "source": o.source, "source_url": o.source_url,
                             "known_from": kf, "retrieved_at": now, "detail": o.detail or None})
            st = self.repo.save_rates(rows)
            dec = derive_decisions(bank, b, obs)
            self.repo.save_decisions([{**{k: v for k, v in d.items() if k not in ("previous_rate_lower",
                                                                                  "new_rate_lower", "note")},
                                       "retrieved_at": now} for d in dec])
            latest = obs[-1] if obs else None
            results["rates"][bank] = {"status": "ok", "observations": len(obs), **st, "decisions": len(dec),
                                      "latest": {"effective_date": latest.effective_date, "rate": latest.rate,
                                                 "rate_lower": latest.rate_lower} if latest else None}
            say(f"{bank}: {len(obs)} observations ({st['inserted']} new), latest "
                f"{latest.rate if latest else '-'}")
        ss = SeriesSources(http, fred)
        for meta in self.cfg.banks.get("official_series") or []:
            try:
                rows = ss.fetch(meta, start - timedelta(days=400))
                results["series"][meta["id"]] = {"status": "ok", **self.repo.save_observations(meta, rows, now)}
            except (ProviderError, ValueError, KeyError) as exc:
                results["series"][meta["id"]] = {"status": "failed", "error": str(exc)[:300]}
                say(f"{meta['id']}: FAILED - {exc}")
        for meta in self.cfg.banks.get("fred_series") or []:
            if not fred:
                results["series"][meta["id"]] = {"status": "not_configured", "reason": "FRED_API_KEY not set"}
                continue
            try:
                rows = ss.fred(meta["id"], start - timedelta(days=400))
                results["series"][meta["id"]] = {"status": "ok", **self.repo.save_observations(
                    {**meta, "source": "fred"}, rows, now, fred_known_from)}
            except ProviderError as exc:
                results["series"][meta["id"]] = {"status": "failed", "error": str(exc)[:300]}
        self.repo.create_run(self.cfg, "macro", None, now, "ok", results)
        self.clear_caches()
        return results

    def macro_overview(self, as_of: datetime | None = None) -> dict[str, Any]:
        t = _utc(as_of)
        ready = self.repo.schema_ready()
        banks = self.cfg.banks.get("banks") or {}
        stale_days = (self.cfg.section("macro").get("stale_after_days") or {}).get("policy_rate", 7)
        out = []
        for bank, b in banks.items():
            row: dict[str, Any] = {"bank": bank, "name": b.get("name"), "currency": b.get("currency"),
                                   "official_url": b.get("official_url"), "rate_source": b.get("rate_source"),
                                   "next_meeting": next_meeting(b, t)}
            if not b.get("rate_source"):
                row.update({"status": "not_sourced", "reason": b.get("note"), "current_rate": None})
            elif not ready:
                row.update({"status": "unavailable", "reason": "Module 04 tables not initialised."})
            else:
                cur = self.repo.rates(bank, t, 1)
                chg = self.repo.rate_changes(bank, t, 2)
                dec = self.repo.decisions(bank, t, 1)
                if not cur:
                    row.update({"status": "no_data", "reason": "Run `python -m aurum.cli intelligence macro-update`.",
                                "current_rate": None})
                else:
                    c = cur[0]
                    retrieved = _utc(c["retrieved_at"])
                    row.update({"status": "ok", "current_rate": c["rate"], "current_rate_lower": c["rate_lower"],
                                "effective_date": c["effective_date"], "known_from": c["known_from"],
                                "previous_rate": chg[-2]["rate"] if len(chg) >= 2 else None,
                                "previous_rate_lower": chg[-2]["rate_lower"] if len(chg) >= 2 else None,
                                "last_change_effective": chg[-1]["effective_date"] if chg else None,
                                "latest_decision": dec[0] if dec else None, "source": c["source"],
                                "source_url": c["source_url"], "retrieved_at": retrieved, "detail": c["detail"],
                                "stale": (t - retrieved) > timedelta(days=stale_days) if retrieved else None})
            out.append(row)
        indicators = []
        for meta in (self.cfg.banks.get("official_series") or []) + (self.cfg.banks.get("fred_series") or []):
            obs = self.repo.observations(meta["id"], t, 3) if ready else []
            stale = None
            if obs:
                limit_days = (self.cfg.section("macro").get("stale_after_days") or {}).get(
                    meta.get("frequency", "monthly"), 75)
                period = str(obs[0]["period"])
                p_date = date.fromisoformat(period if len(period) == 10 else f"{period[:7]}-01")
                stale = (t.date() - p_date).days > int(limit_days) + (31 if len(period) == 7 else 0)
            indicators.append({"id": meta["id"], "name": meta["name"], "category": meta.get("category"),
                               "currency": meta.get("currency"), "unit": meta.get("unit"),
                               "source": meta.get("source", "fred"),
                               "status": ("stale" if stale else "ok") if obs else
                               ("not_configured" if meta in (self.cfg.banks.get("fred_series") or [])
                                and not self.settings.fred_api_key else "no_data"),
                               "stale": stale, "latest": obs[0] if obs else None,
                               "previous": obs[1] if len(obs) > 1 else None})
        return {"as_of": t, "central_banks": out, "indicators": indicators,
                "note": "Official sources only. Values are point-in-time: nothing published after as_of is shown."}

    # ------------------------------------------------------------------------------------------ news
    def refresh_news(self) -> dict[str, Any]:
        self._require_schema()
        if not self.settings.news_enabled:
            return {"status": "disabled"}
        items, errors = OfficialFeedProvider(self.cfg.banks.get("official_feeds") or [], self.http()).fetch()
        if self.settings.economic_calendar_provider == "finnhub" and self.settings.economic_calendar_api_key:
            key = self.settings.economic_calendar_api_key.get_secret_value()
            more, err2 = FinnhubNewsProvider(key, self.http([key])).fetch()
            items += more
            errors += err2
        st = self.repo.save_news(items)
        if errors:
            self.repo.record_issues(None, None, "news", [{"issue": "feed_failed", **e, "severity": "warning"}
                                                         for e in errors])
        self.repo.create_run(self.cfg, "news", None, self.now(), "ok" if not errors else "partial",
                             {"received": len(items), **st, "errors": errors})
        self.clear_caches()
        return {"received": len(items), **st, "errors": errors}

    def news(self, as_of: datetime | None = None, symbol: str | None = None, page: int = 1,
             page_size: int = 30) -> dict[str, Any]:
        self._require_schema()
        cur = None
        if symbol:
            inst = self.instrument(symbol)
            cur = sorted(c for c, ms in self.impact.by_currency.items()
                         if any(m["instrument"] == inst.symbol for m in ms))
        items, total = self.repo.news(_utc(as_of) if as_of else None, cur, page_size, (page - 1) * page_size)
        items = [{**it, "tone": headline_tone(it["headline"], it.get("category"), it.get("description"))}
                 for it in items]
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "sentiment_summary": summarise_tone(items),
                "note": "Headlines exactly as published, with their source. Official announcements are from the "
                        "issuing institution; nothing here is AI-generated. 'tone' is AURUM's rule-based reading of "
                        "the headline wording (matched terms listed), not part of the news."}

    def news_impact(self, hours_back: int = 24, hours_ahead: int = 168, importance: list[str] | None = None,
                    currencies: list[str] | None = None) -> dict[str, Any]:
        """High-impact events with conditional scenarios, measured past reactions and (once released) the outcome,
        plus high-impact headlines. See ``aurum.market_intelligence.news_impact``."""
        from aurum.market_intelligence.news_impact import NewsImpact
        self._require_schema()
        return NewsImpact(self.repo, self.candles, self.impact, self.symbols, self.now).report(
            hours_back, hours_ahead, importance, currencies)

    def news_context(self, symbol: str, as_of: datetime, hours: int = 72) -> dict[str, Any]:
        """Headlines published in the ``hours`` before ``as_of`` that concern the instrument's currencies."""
        if not self.repo.schema_ready():
            return {"available": False, "reason": "Module 04 tables not initialised.", "items": []}
        ccy = self.symbol_currencies(symbol)
        items, _ = self.repo.news(as_of, ccy or None, 200, 0)
        items = [{**it, "tone": headline_tone(it["headline"], it.get("category"), it.get("description"))}
                 for it in items if it.get("published_at") and _utc(it["published_at"]) >= as_of - timedelta(hours=hours)]
        return {"available": True, "window_hours": hours, "currencies": ccy, "count": len(items),
                "latest": [{k: it.get(k) for k in ("headline", "url", "source", "source_type", "published_at",
                                                    "currencies", "tone")} for it in items[:5]],
                "sentiment_summary": summarise_tone(items)}

    # ------------------------------------------------------------------------------------------ full analysis
    def analyze(self, symbol: str, as_of: datetime | None = None, persist: bool = False,
                include_module03: bool = True) -> dict[str, Any]:
        """Full analysis. Non-persisted results are cached for ``performance.analysis_cache_seconds`` per (symbol,
        as_of minute, Module 03 flag, config fingerprint); ``cache`` in the result says whether it was reused."""
        import copy
        import time as _time
        from aurum.market_intelligence.module_adapters import minute_key
        inst, t = self.instrument(symbol), _utc(as_of)
        key = (inst.symbol, minute_key(t), include_module03, self.cfg.fingerprint)
        hit = self._analysis_cache.get(key)
        if not persist and hit is not None and _time.monotonic() - hit[0] <= self.analysis_ttl:
            out = copy.deepcopy(hit[1])
            out["cache"] = {"hit": True, "computed_at": hit[1]["generated_at"], "ttl_seconds": self.analysis_ttl}
            return out
        result = self._analyze(inst, t, persist, include_module03)
        result["cache"] = {"hit": False, "computed_at": result["generated_at"], "ttl_seconds": self.analysis_ttl}
        if len(self._analysis_cache) >= 128:
            self._analysis_cache.pop(min(self._analysis_cache, key=lambda k: self._analysis_cache[k][0]))
        self._analysis_cache[key] = (_time.monotonic(), copy.deepcopy(result))
        return result

    def _analyze(self, inst: InstrumentConfig, t: datetime, persist: bool, include_module03: bool) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        sess = self.sessions(t, inst.symbol)
        lv = self.levels(inst.symbol, t)
        issues += [{"component": "levels", **i} for i in lv["issues"]]
        m03 = self.module03_context(inst.symbol, t) if include_module03 else {"available": False,
                                                                               "reason": "disabled for this request"}
        confl = self.m03.confluence(inst.symbol, t) if include_module03 else None
        liq = self.liquidity(inst.symbol, t, lv, m03)
        stats = self.statistics(inst.symbol, t)
        vol = self.volatility(inst.symbol, t, stats)
        risk = self.event_risk(inst.symbol, t, vol)
        h1 = self.candles.frame(inst.symbol, str(self.cfg.section("conditions").get("timeframe") or "H1"), t)
        cond = cond_mod.classify(self.cfg, inst.symbol, t, frame=h1, volatility=vol, risk=risk, sessions=sess,
                                 liquidity=liq, m03=m03, confluence=confl)
        primary_vol = vol["timeframes"].get(vol["regime_timeframe"]) or {}
        scores = scoring.score_bundle(
            self.cfg, t,
            event_risk=scoring.event_risk_score(self.cfg, risk, primary_vol, risk.get("historical_context")),
            session_activity=scoring.session_activity_score(self.cfg, stats, primary_vol, sess["active_sessions"],
                                                            [o["name"] for o in sess["overlaps"] if o["active"]]),
            volatility=scoring.volatility_score(primary_vol))
        cot = self.m02.context(inst.symbol, t)
        if stats.get("available") is False:
            issues.append({"component": "statistics", "issue": "no_candles", "message": stats.get("reason")})
        if primary_vol.get("status") != "ok":
            issues.append({"component": "volatility", "issue": "insufficient_data", "message": primary_vol.get("reason")})
        result = {"symbol": inst.symbol, "as_of": t, "generated_at": self.now(), "module_version": MODULE_VERSION,
                  "methodology_version": self.cfg.methodology_version, "config_fingerprint": self.cfg.fingerprint,
                  "sessions": sess, "levels": lv, "liquidity": liq, "statistics": stats, "volatility": vol,
                  "event_risk": risk, "conditions": cond, "scores": scores,
                  "news": self.news_context(inst.symbol, t),
                  "integrations": {"module_01": {"available": not h1.empty,
                                                 "candles_h1": int(len(h1)),
                                                 "last_candle": h1["ts"].iloc[-1].to_pydatetime() if len(h1) else None},
                                   "module_02": {k: cot.get(k) for k in ("available", "stance", "lean", "reason",
                                                                         "interpretation")},
                                   "module_03": {k: m03.get(k) for k in ("available", "timeframe", "external_bias",
                                                                         "external_state", "reason")},
                                   "economic_calendar": {"available": bool((risk.get("coverage") or {}).get("complete")),
                                                         **(risk.get("coverage") or {})}},
                  "data_quality": {"issues": issues, "calendar_coverage": risk.get("coverage")},
                  "disclaimer": DISCLAIMER, "run_uid": None}
        if persist:
            self._require_schema()
            uid = self.repo.create_run(self.cfg, "analysis", inst.symbol, t, "ok",
                                       {"tags": cond["tags"], "event_risk": risk["state"], "regime": vol["regime"],
                                        "scores": {k: (scores.get(k) or {}).get("score")
                                                   for k in ("event_risk", "session_activity", "volatility")}})
            self.repo.save_levels(uid, t, lv["levels"])
            self.repo.save_liquidity(uid, t, [e for e in liq["events"]])
            if stats.get("available"):
                self.repo.save_statistics(uid, inst.symbol, t, stats)
            self.repo.save_volatility(uid, inst.symbol, t, vol)
            self.repo.save_conditions(uid, inst.symbol, t, cond)
            self.repo.save_scores(uid, inst.symbol, t, self.cfg, scores)
            if issues:
                self.repo.record_issues(uid, inst.symbol, "analysis", issues)
            self.repo.save_definitions(self.cfg)
            self.repo.save_sessions(session_history(self.cfg, t - timedelta(days=2), t + timedelta(days=2),
                                                    include_kill_zones=True))
            result["run_uid"] = uid
        return result

    # ------------------------------------------------------------------------------------------ status
    def meeting_staleness(self) -> dict[str, Any]:
        from aurum.market_intelligence.meetings import staleness
        return {b: staleness(cfg, self.now().date()) for b, cfg in (self.cfg.banks.get("banks") or {}).items()}

    def meetings_check(self, write: bool = False) -> dict[str, Any]:
        """Compare configured meeting dates with the banks' official calendars (optionally write the overlay)."""
        from aurum.market_intelligence import meetings
        res = meetings.check(self.cfg.banks, self.http(), self.now().date())
        out: dict[str, Any] = {"banks": res, "written": None}
        if write:
            out["written"] = str(meetings.write_overlay(res, now=self.now()))
        return out

    def status(self) -> dict[str, Any]:
        ready = self.repo.schema_ready()
        try:
            missing = self.repo.missing_tables()
        except Exception as exc:  # noqa: BLE001
            missing = [f"database unreachable ({type(exc).__name__})"]
        db_sources = {s["provider"]: s for s in self.repo.sources()} if ready else {}
        registry = []
        for p in provider_registry(self.settings, self.cfg):
            s = db_sources.get(p["provider"], {})
            registry.append({**p, "status": s.get("status") or ("not_configured" if not p["configured"] else "never_run"),
                             "last_success_at": s.get("last_success_at"), "last_attempt_at": s.get("last_attempt_at"),
                             "last_error": s.get("last_error"), "last_event_count": s.get("last_event_count")})
        return {"module_version": MODULE_VERSION, "methodology_version": self.cfg.methodology_version,
                "config_fingerprint": self.cfg.fingerprint, "schema_ready": ready, "missing_tables": missing,
                "calendar_providers": registry, "calendar_coverage": self.calendar_coverage() if ready else None,
                "official_data_enabled": self.settings.official_data_enabled,
                "fred_configured": self.settings.fred_api_key is not None,
                "last_ingestions": self.repo.ingestion_logs(10) if ready else [],
                "row_counts": self.repo.table_counts() if ready else {},
                "instruments": self.symbols(), "trade_execution": False,
                "meeting_schedules": self.meeting_staleness(),
                "note": "Read-only analytical module: no order placement, no account access."}
