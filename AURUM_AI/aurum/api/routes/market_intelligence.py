"""/api/v1/intelligence/* - Module 04 endpoints (read-only market intelligence; no trading endpoints).

Sessions     GET  /sessions/current | /sessions/upcoming | /sessions/history | /sessions/overlaps |
                  /sessions/kill-zones | /sessions/levels/{symbol} | /sessions/statistics/{symbol} |
                  /sessions/liquidity/{symbol}
Calendar     GET  /calendar | /calendar/upcoming | /calendar/status | /calendar/impact/{symbol} | /calendar/{event_id}
             POST /calendar/refresh                     admin, background job
Macro        GET  /macro/overview | /macro/central-banks | /macro/indicators | /macro/rates
             POST /macro/refresh                        admin, background job
News         GET  /news                                 POST /news/refresh (admin)
Volatility   GET  /volatility/{symbol} | /volatility/{symbol}/sessions | /volatility/{symbol}/regime
Market       GET  /market/{symbol} | /market/{symbol}/conditions | /market/{symbol}/risk
             POST /analyze/{symbol}                     admin: analyse and persist
System       GET  /status | /config | /jobs/{job_id}

All timestamps are ISO-8601 UTC; keys are camelCase. GET endpoints are open like the rest of the API; POST endpoints
need the X-AURUM-Admin-Key header. When a provider is not configured the response says so - nothing is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from aurum.api.app import AdminDep, AppState, camelize
from aurum.market_intelligence.service import MarketIntelligenceService, SchemaNotReady, UnknownInstrumentError

UTC = timezone.utc


class CalendarRefreshRequest(BaseModel):
    days_back: int = Field(7, ge=0, le=90, alias="daysBack")
    days_ahead: int = Field(21, ge=1, le=120, alias="daysAhead")
    model_config = {"populate_by_name": True}


class AnalyzeRequest(BaseModel):
    as_of: datetime | None = Field(None, alias="asOf")
    persist: bool = True
    model_config = {"populate_by_name": True}


class JobAccepted(BaseModel):
    jobId: str
    type: str
    status: str


def _csv(v: str | None, upper: bool = False) -> list[str] | None:
    if not v:
        return None
    out = [x.strip() for x in v.split(",") if x.strip()]
    return [x.upper() for x in out] if upper else out


def build_router(state: AppState, service: MarketIntelligenceService | None = None) -> APIRouter:
    r = APIRouter()
    holder: dict[str, MarketIntelligenceService] = {}

    def svc() -> MarketIntelligenceService:
        if "svc" not in holder:
            holder["svc"] = service or MarketIntelligenceService(state.settings, state.engine)
        return holder["svc"]

    def run(fn, *a, **k):
        try:
            return fn(*a, **k)
        except UnknownInstrumentError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except SchemaNotReady as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - database down etc.; details stay in the server log
            if "OperationalError" in type(exc).__name__ or "ProgrammingError" in type(exc).__name__:
                raise HTTPException(503, f"Data store unavailable ({type(exc).__name__}).") from exc
            raise

    def as_of_q(v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def submit(job_type: str, params: dict[str, Any], fn) -> dict[str, Any]:
        job = state.jobs.submit(job_type, params, fn)
        return JobAccepted(jobId=job.id, type=job.type, status=job.status).model_dump()

    # ------------------------------------------------------------------------------------ sessions
    @r.get("/sessions/current", summary="Session, overlap and kill-zone status now (or at asOf)")
    def sessions_current(as_of: datetime | None = Query(None, alias="asOf"), symbol: str | None = None):
        return camelize(run(svc().sessions, as_of_q(as_of), symbol))

    @r.get("/sessions/upcoming", summary="Next opening of every session and kill zone")
    def sessions_upcoming(as_of: datetime | None = Query(None, alias="asOf"), symbol: str | None = None):
        snap = run(svc().sessions, as_of_q(as_of), symbol)
        rows = [{"name": s["name"], "kind": s["kind"], **(s["next"] or {}), "minutes_until_open": s["minutes_until_open"],
                 "status": s["status"]} for s in snap["sessions"] + snap["kill_zones"] if s["next"]]
        return camelize({"as_of": snap["as_of"], "items": sorted(rows, key=lambda x: x["start_utc"])})

    @r.get("/sessions/history", summary="Past / future session occurrences in UTC")
    def sessions_history(start: datetime | None = Query(None, description="default: 7 days before end"),
                         end: datetime | None = Query(None, description="default: now"),
                         names: str | None = Query(None, description="comma-separated session / kill-zone names"),
                         include_kill_zones: bool = Query(False, alias="includeKillZones"),
                         page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=1000, alias="pageSize")):
        e = as_of_q(end) or datetime.now(timezone.utc)
        b = as_of_q(start) or e - timedelta(days=7)
        items = run(svc().session_history, b, e, _csv(names), include_kill_zones)
        return camelize({"items": items[(page - 1) * page_size: page * page_size], "total": len(items),
                         "page": page, "page_size": page_size})

    @r.get("/sessions/overlaps", summary="Overlap / transition windows with measured historical activity")
    def sessions_overlaps(as_of: datetime | None = Query(None, alias="asOf"), symbol: str | None = None):
        snap = run(svc().sessions, as_of_q(as_of), symbol)
        activity = {}
        if symbol:
            st = run(svc().statistics, symbol, as_of_q(as_of))
            activity = {k: v.get("activity") for k, v in (st.get("sessions") or {}).items() if v.get("kind") == "overlap"}
        return camelize({"as_of": snap["as_of"], "items": [{**o, "historical_activity": activity.get(o["name"])}
                                                           for o in snap["overlaps"]],
                         "note": "Overlaps are not trading opportunities by themselves; activity is measured "
                                 "from the instrument's own candles when a symbol is given."})

    @r.get("/sessions/kill-zones", summary="Kill-zone status (per-instrument definitions with ?symbol=)")
    def sessions_kill_zones(as_of: datetime | None = Query(None, alias="asOf"), symbol: str | None = None):
        snap = run(svc().sessions, as_of_q(as_of), symbol)
        return camelize({"as_of": snap["as_of"], "symbol": snap["symbol"], "items": snap["kill_zones"],
                         "active": snap["active_kill_zones"], "next": snap["next_kill_zone"],
                         "note": "Configurable reference windows, not market rules."})

    @r.get("/sessions/levels/{symbol}", summary="Previous / current session, day, week and month highs and lows")
    def session_levels(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().levels, symbol, as_of_q(as_of)))

    @r.get("/sessions/statistics/{symbol}", summary="Historical session statistics (completed sessions)")
    def session_stats(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().statistics, symbol, as_of_q(as_of)))

    @r.get("/sessions/liquidity/{symbol}", summary="Session sweeps, breakouts and false breakouts")
    def session_liquidity(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().liquidity, symbol, as_of_q(as_of)))

    # ------------------------------------------------------------------------------------ calendar
    @r.get("/calendar", summary="Stored economic-calendar events (paginated, filterable, point-in-time with asOf)")
    def calendar(start: datetime | None = None, end: datetime | None = None,
                 range_: Literal["today", "tomorrow", "this_week", "next_week"] | None = Query(None, alias="range"),
                 as_of: datetime | None = Query(None, alias="asOf"), currency: str | None = None,
                 importance: str | None = None, category: str | None = None, instrument: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500, alias="pageSize")):
        s, e = as_of_q(start), as_of_q(end)
        if range_:
            ref = as_of_q(as_of) or datetime.now(UTC)
            day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
            s, e = {"today": (day, day + timedelta(days=1)),
                    "tomorrow": (day + timedelta(days=1), day + timedelta(days=2)),
                    "this_week": (day - timedelta(days=day.weekday()), day + timedelta(days=7 - day.weekday())),
                    "next_week": (day + timedelta(days=7 - day.weekday()), day + timedelta(days=14 - day.weekday()))}[range_]
        imp = _csv(importance)
        if imp and any(i not in ("low", "medium", "high") for i in imp):
            raise HTTPException(422, "importance must be low, medium and/or high.")
        return camelize(run(svc().calendar, s, e, as_of_q(as_of), _csv(currency, True), imp, _csv(category),
                            instrument, page, page_size))

    @r.get("/calendar/upcoming", summary="Upcoming events (next N hours)")
    def calendar_upcoming(hours: int = Query(48, ge=1, le=720), currency: str | None = None,
                          importance: str | None = None, instrument: str | None = None,
                          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500, alias="pageSize")):
        now = datetime.now(UTC)
        return camelize(run(svc().calendar, now, now + timedelta(hours=hours), None, _csv(currency, True),
                            _csv(importance), None, instrument, page, page_size))

    @r.get("/calendar/status", summary="Provider connectivity and calendar coverage")
    def calendar_status():
        st = run(svc().status)
        return camelize({k: st[k] for k in ("calendar_providers", "calendar_coverage", "last_ingestions",
                                            "schema_ready", "fred_configured")})

    @r.get("/calendar/impact/{symbol}", summary="Events relevant to an instrument + its event-risk state")
    def calendar_impact(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().event_risk, symbol, as_of_q(as_of)))

    @r.get("/calendar/{event_id}", summary="One event with its revision history and original payload")
    def calendar_event(event_id: int):
        ev = run(svc().repo.event, event_id)
        if ev is None:
            raise HTTPException(404, f"Event {event_id} not found.")
        return camelize(ev)

    @r.post("/calendar/refresh", status_code=202, dependencies=[AdminDep], summary="Admin: ingest calendar (background)")
    def calendar_refresh(req: CalendarRefreshRequest | None = None):
        req = req or CalendarRefreshRequest()
        run(svc()._require_schema)
        return submit("intelligence.calendar.refresh", req.model_dump(),
                      lambda say: camelize(svc().refresh_calendar(req.days_back, req.days_ahead, say)))

    # ------------------------------------------------------------------------------------ macro / news
    @r.get("/macro/overview", summary="Central-bank rates, decisions, meetings and indicators (point-in-time)")
    def macro_overview(as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().macro_overview, as_of_q(as_of)))

    @r.get("/macro/central-banks", summary="Central-bank policy rates, latest decision and next meeting")
    def macro_banks(as_of: datetime | None = Query(None, alias="asOf")):
        o = run(svc().macro_overview, as_of_q(as_of))
        return camelize({"as_of": o["as_of"], "items": o["central_banks"]})

    @r.get("/macro/indicators", summary="Macro indicators (point-in-time; no later revisions)")
    def macro_indicators(as_of: datetime | None = Query(None, alias="asOf")):
        o = run(svc().macro_overview, as_of_q(as_of))
        return camelize({"as_of": o["as_of"], "items": o["indicators"]})

    @r.get("/macro/rates", summary="Policy-rate history for one bank")
    def macro_rates(bank: str = Query(...), as_of: datetime | None = Query(None, alias="asOf"),
                    limit: int = Query(30, ge=1, le=2000)):
        b = bank.lower()
        if b not in (svc().cfg.banks.get("banks") or {}):
            raise HTTPException(404, f"Unknown central bank '{bank}'.")
        run(svc()._require_schema)
        return camelize({"bank": b, "changes": run(svc().repo.rate_changes, b, as_of_q(as_of), limit),
                         "decisions": run(svc().repo.decisions, b, as_of_q(as_of), limit)})

    @r.post("/macro/refresh", status_code=202, dependencies=[AdminDep], summary="Admin: refresh official macro data")
    def macro_refresh():
        run(svc()._require_schema)
        return submit("intelligence.macro.refresh", {}, lambda say: camelize(svc().refresh_macro(say)))

    @r.get("/news", summary="Stored headlines (official announcements / reported news), as published")
    def news(symbol: str | None = None, as_of: datetime | None = Query(None, alias="asOf"),
             page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200, alias="pageSize")):
        return camelize(run(svc().news, as_of_q(as_of), symbol, page, page_size))

    @r.post("/news/refresh", status_code=202, dependencies=[AdminDep], summary="Admin: refresh official news feeds")
    def news_refresh():
        run(svc()._require_schema)
        return submit("intelligence.news.refresh", {}, lambda say: camelize(svc().refresh_news()))

    # ------------------------------------------------------------------------------------ volatility
    @r.get("/volatility/{symbol}", summary="Volatility report (ATR, realised vol, percentiles, regime)")
    def volatility(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        return camelize(run(svc().volatility, symbol, as_of_q(as_of)))

    @r.get("/volatility/{symbol}/sessions", summary="Per-session volatility vs history")
    def volatility_sessions(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        v = run(svc().volatility, symbol, as_of_q(as_of))
        return camelize({"symbol": v["symbol"], "as_of": v["as_of"], "sessions": v["sessions"]})

    @r.get("/volatility/{symbol}/regime", summary="Volatility regime with the reason")
    def volatility_regime(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        v = run(svc().volatility, symbol, as_of_q(as_of))
        return camelize({"symbol": v["symbol"], "as_of": v["as_of"], "regime": v["regime"],
                         "timeframe": v["regime_timeframe"], "reason": v["regime_reason"],
                         "per_timeframe": {tf: {k: x.get(k) for k in ("regime", "atr", "atr_percentile", "status",
                                                                       "reason")} for tf, x in v["timeframes"].items()}})

    # ------------------------------------------------------------------------------------ market
    @r.get("/market/{symbol}", summary="Full market-intelligence report (not persisted)")
    def market(symbol: str, as_of: datetime | None = Query(None, alias="asOf"),
               detail: Literal["summary", "full"] = Query("summary"),
               include_module03: bool = Query(True, alias="includeModule03")):
        res = run(svc().analyze, symbol, as_of_q(as_of), False, include_module03)
        if detail == "summary":
            res = {**res, "statistics": {k: res["statistics"].get(k) for k in ("available", "timeframe",
                                                                                 "breakout_frequency", "daily_range",
                                                                                 "weekly_range")},
                   "levels": {"levels": res["levels"]["levels"], "issues": res["levels"]["issues"]}}
        return camelize(res)

    @r.get("/market/{symbol}/conditions", summary="Market-condition tags with evidence")
    def market_conditions(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        res = run(svc().analyze, symbol, as_of_q(as_of))
        return camelize({**res["conditions"], "scores": res["scores"]})

    @r.get("/market/{symbol}/risk", summary="Event-risk state and score")
    def market_risk(symbol: str, as_of: datetime | None = Query(None, alias="asOf")):
        res = run(svc().analyze, symbol, as_of_q(as_of))
        return camelize({**res["event_risk"], "score": res["scores"]["event_risk"]})

    @r.post("/analyze/{symbol}", status_code=201, dependencies=[AdminDep], summary="Admin: analyse and persist")
    def analyze(symbol: str, req: AnalyzeRequest | None = None):
        req = req or AnalyzeRequest()
        res = run(svc().analyze, symbol, as_of_q(req.as_of), req.persist)
        return camelize({"symbol": res["symbol"], "as_of": res["as_of"], "run_uid": res["run_uid"],
                         "tags": res["conditions"]["tags"], "event_risk": res["event_risk"]["state"],
                         "regime": res["volatility"]["regime"],
                         "scores": {k: (res["scores"].get(k) or {}).get("score")
                                    for k in ("event_risk", "session_activity", "volatility")}})

    # ------------------------------------------------------------------------------------ system
    @r.get("/status", summary="Module 04 status: schema, providers, coverage, row counts")
    def status():
        return camelize(run(svc().status))

    @r.get("/config", summary="Effective Module 04 configuration")
    def config():
        return camelize(svc().cfg.to_dict())

    @r.get("/jobs/{job_id}", summary="Background job status")
    def job(job_id: str):
        j = state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job id.")
        return camelize(j.public())

    return r
