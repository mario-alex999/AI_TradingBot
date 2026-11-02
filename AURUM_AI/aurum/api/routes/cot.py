"""/api/v1/cot/* - Module 02 endpoints. All responses use camelCase keys; timestamps are ISO-8601 UTC.

Read endpoints serve stored data even when the CFTC is unreachable (freshness is reported explicitly).
POST endpoints start background jobs (HTTP 202) and require the X-AURUM-Admin-Key header.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from aurum.api.app import AdminDep, AppState, camelize
from aurum.cot.fields import CATEGORY_FIELDS, CATEGORY_LABELS

ReportType = Literal["legacy", "tff", "disaggregated"]
Severity = Literal["info", "warning", "error", "critical"]


class BackfillRequest(BaseModel):
    years: float = Field(4.0, gt=0, le=40, description="Years of history (ignored when start is given)")
    start: date | None = None
    end: date | None = None
    report_types: list[ReportType] | None = Field(None, alias="reportTypes")
    resume: bool = True
    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _order(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class UpdateRequest(BaseModel):
    report_types: list[ReportType] | None = Field(None, alias="reportTypes")
    model_config = {"populate_by_name": True}


class CalculateRequest(BaseModel):
    instruments: list[str] | None = None

    @field_validator("instruments")
    @classmethod
    def _upper(cls, v):
        return [s.upper() for s in v] if v else v


def _page(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    return camelize({"items": items, "total": total, "page": page, "page_size": page_size})


def _df_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return [{k: (None if (isinstance(v, float) and pd.isna(v)) or v is pd.NaT else v) for k, v in r.items()}
            for r in df.to_dict(orient="records")]


def build_router(state: AppState) -> APIRouter:
    r = APIRouter()
    svc, cfg, repo = state.service, state.cfg, state.service.repo
    probe_cache: dict[str, Any] = {"at": 0.0, "rows": None}

    def ids() -> dict[tuple[str, str], int]:
        if not svc._map_ids:
            svc.sync()
        return svc._map_ids

    def check_symbol(symbol: str) -> str:
        s = symbol.upper()
        if s not in cfg.symbols:
            raise HTTPException(404, f"Unknown instrument '{symbol}'. Available: {', '.join(cfg.symbols)}")
        return s

    def mapping_id(symbol: str, report_type: str | None) -> tuple[int, str]:
        maps = cfg.mappings_for(symbol, report_type)
        if not maps:
            raise HTTPException(404, f"No {report_type or ''} COT mapping for {symbol}".replace("  ", " "))
        m = maps[0]
        return ids()[(m.aurum_symbol, m.report_type)], m.report_type

    def price_loader():
        from aurum.cot.cli import _price_loader
        return _price_loader(state.engine)

    def require_schema() -> None:
        try:
            ok = repo.schema_ready()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"Database unavailable ({type(exc).__name__}).") from exc
        if not ok:
            raise HTTPException(503, "COT tables missing - run: python -m aurum.cli init-db")

    # ------------------------------------------------------------------ status / config
    @r.get("/status", summary="Engine status, freshness, record counts, quality warnings")
    def status(probe: bool = Query(False, description="Also test CFTC connectivity now (cached 10 min)")):
        require_schema()
        from aurum.cot.intelligence import freshness
        st = repo.status_summary()
        now = datetime.now(timezone.utc)
        series = []
        for m in cfg.mappings:
            mid = ids().get((m.aurum_symbol, m.report_type))
            rep = repo.report_frame(mapping_id=mid) if mid else pd.DataFrame()
            if rep.empty:
                series.append({"instrument": m.aurum_symbol, "report_type": m.report_type, "reports": 0,
                               "freshness": {"status": "no_data"}})
                continue
            last = rep.iloc[-1]
            fr = freshness(last["release_timestamp"], last["report_date"], cfg, now)
            series.append({"instrument": m.aurum_symbol, "report_type": m.report_type, "reports": len(rep),
                           "latest_report_date": last["report_date"], "latest_release_date": last["release_date"],
                           "freshness": fr})
        cftc = None
        if probe:
            if probe_cache["rows"] is None or time.time() - probe_cache["at"] > 600:
                probe_cache["rows"], probe_cache["at"] = svc.client.check(), time.time()
            rows = probe_cache["rows"]
            cftc = {"checked": True, "sources": rows,
                    "api_reachable": any(x["ok"] for x in rows if x["source"] == "socrata"),
                    "files_reachable": any(x["ok"] for x in rows if x["source"] == "files")}
        pending = [s for s in series if s["freshness"].get("status") in ("update_due", "stale", "no_data")]
        logs, _ = repo.logs(limit=1)
        return camelize({"engine": "ok" if st["datasets"] else "empty", "configured_source": cfg.source,
                         "cftc": cftc or {"checked": False, "note": "Pass ?probe=true to test CFTC connectivity."},
                         "datasets": st["datasets"], "series": series, "contracts_monitored": len(cfg.mappings),
                         "positions_stored": st["positions"], "metrics_stored": st["metrics"],
                         "last_successful_ingestion": st["last_successful_ingestion"],
                         "last_ingestion": logs[0] if logs else None, "failed_ingestions": st["failed_ingestions"],
                         "pending_updates": len(pending), "open_issues": st["open_issues"],
                         "running_jobs": [j.public() for j in state.jobs.list() if j.status in ("queued", "running")]})

    @r.get("/datasets", summary="Supported CFTC report families and their configuration")
    def datasets():
        return camelize([{"report_type": d.key, "name": d.name, "dataset_id": d.dataset_id,
                          "dataset_id_status": d.dataset_id_status, "endpoint": d.endpoint(cfg.socrata.base_url),
                          "report_scope": d.report_scope, "history_start": d.history_start,
                          "history_file": d.history_file, "yearly_file": d.yearly_file, "description": d.description,
                          "categories": [{"key": c, "label": CATEGORY_LABELS.get(c, c)} for c in CATEGORY_FIELDS[d.key]]}
                         for d in cfg.datasets.values()])

    @r.get("/instruments", summary="AURUM instruments and their futures mappings")
    def instruments():
        out = []
        for s in cfg.symbols:
            out.append({"instrument": s, "mappings": [{
                "report_type": m.report_type, "cftc_contract_name": m.cftc_contract_name,
                "cftc_contract_code": m.cftc_contract_code, "exchange": m.exchange,
                "contract_description": m.contract_description, "currency": m.currency,
                "mapping_status": m.mapping_status, "verification_source": m.verification_source,
                "verified_at": m.verified_at, "primary_category": m.primary_category,
                "primary_category_label": CATEGORY_LABELS.get(m.primary_category)} for m in cfg.mappings_for(s)]})
        return camelize(out)

    # ------------------------------------------------------------------ data
    @r.get("/reports", summary="Stored COT reports (no payload); filter and paginate")
    def reports(instrument: str | None = None, contract: str | None = Query(None, max_length=16),
                report_type: ReportType | None = Query(None, alias="reportType"),
                start: date | None = None, end: date | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500, alias="pageSize")):
        require_schema()
        mid = None
        if instrument:
            s = check_symbol(instrument)
            if report_type is None and len(cfg.mappings_for(s)) > 1:
                df = pd.concat([repo.report_frame(mapping_id=ids()[(m.aurum_symbol, m.report_type)], start=start, end=end)
                                for m in cfg.mappings_for(s)], ignore_index=True)
            else:
                mid, _ = mapping_id(s, report_type)
                df = repo.report_frame(mapping_id=mid, report_type=report_type, start=start, end=end)
        else:
            df = repo.report_frame(report_type=report_type, start=start, end=end)
        if contract:
            df = df[df["contract_code"] == contract]
        df = df.sort_values(["report_date", "report_type"], ascending=[False, True]) if not df.empty else df
        total = len(df)
        items = _df_records(df.iloc[(page - 1) * page_size: page * page_size])
        return _page(items, total, page, page_size)

    @r.get("/reports/{report_id}", summary="One report with the verbatim CFTC payload, positions, metrics, revisions")
    def report_detail(report_id: int):
        require_schema()
        d = repo.report_detail(report_id)
        if d is None:
            raise HTTPException(404, f"Report {report_id} not found")
        payload = d.pop("source_payload")
        out = camelize(d)
        out["sourcePayload"] = payload          # original CFTC field names, untouched
        return out

    @r.get("/positions", summary="Participant positions for one report (by id, or instrument+type+date)")
    def positions(report_id: int | None = Query(None, alias="reportId"), instrument: str | None = None,
                  report_type: ReportType | None = Query(None, alias="reportType"), report_date: date | None = Query(None, alias="reportDate")):
        require_schema()
        if report_id is None:
            if not instrument:
                raise HTTPException(422, "Provide reportId, or instrument (+ optional reportType, reportDate)")
            mid, rt = mapping_id(check_symbol(instrument), report_type)
            rep = repo.report_frame(mapping_id=mid)
            if report_date:
                rep = rep[rep["report_date"] == report_date]
            if rep.empty:
                raise HTTPException(404, "No stored report matches")
            report_id = int(rep.iloc[-1]["id"])
        d = repo.report_detail(report_id)
        if d is None:
            raise HTTPException(404, f"Report {report_id} not found")
        d.pop("source_payload")
        for p in d["positions"]:
            p["label"] = CATEGORY_LABELS.get(p["trader_category"], p["trader_category"])
        return camelize({k: d[k] for k in ("id", "report_type", "contract_code", "contract_market_name", "report_date",
                                           "release_date", "release_timestamp", "open_interest", "positions")})

    @r.get("/metrics", summary="Calculated metrics; filter and paginate")
    def metrics(instrument: str | None = None, report_type: ReportType | None = Query(None, alias="reportType"),
                category: str | None = Query(None, max_length=32), start: date | None = None, end: date | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000, alias="pageSize")):
        require_schema()
        mid = mapping_id(check_symbol(instrument), report_type)[0] if instrument else None
        df = repo.metrics_frame(mapping_id=mid, category=category, start=start, end=end)
        if report_type and not df.empty:
            df = df[df["report_type"] == report_type]
        df = df.sort_values(["report_date", "trader_category"], ascending=[False, True]) if not df.empty else df
        return _page(_df_records(df.iloc[(page - 1) * page_size: page * page_size]), len(df), page, page_size)

    @r.get("/history/{instrument}", summary="Historical positioning + metrics time series for charts")
    def history(instrument: str, report_type: ReportType | None = Query(None, alias="reportType"),
                start: date | None = None, end: date | None = None):
        require_schema()
        s = check_symbol(instrument)
        mid, rt = mapping_id(s, report_type)
        m = cfg.mappings_for(s, rt)[0]
        pos = repo.positions_frame(mid)
        met = repo.metrics_frame(mapping_id=mid, start=start, end=end)
        reps = repo.report_frame(mapping_id=mid, start=start, end=end)
        series = []
        if not reps.empty:
            pos_idx = {(r_["report_id"], r_["trader_category"]): r_ for r_ in _df_records(pos)}
            met_idx = {(r_["cot_report_id"], r_["trader_category"]): r_ for r_ in _df_records(met)}
            for rep in _df_records(reps):
                cats = []        # list (not a dict keyed by category) so camelCase conversion never touches the keys
                for cat in CATEGORY_FIELDS[rt]:
                    p, mm = pos_idx.get((rep["id"], cat)), met_idx.get((rep["id"], cat))
                    if p is None:
                        continue
                    cats.append({"category": cat, "long": p["long_positions"], "short": p["short_positions"],
                                 "spreading": p["spreading_positions"], "net": p["net_positions"],
                                 **({k: mm[k] for k in ("weekly_net_change", "net_position_pct_oi",
                                                        "historical_percentile_12m", "historical_percentile_24m",
                                                        "historical_percentile_36m", "historical_percentile_48m",
                                                        "cot_index", "z_score", "positioning_strength", "change_type")}
                                    if mm else {})})
                series.append({"report_id": rep["id"], "report_date": rep["report_date"],
                               "release_date": rep["release_date"], "release_timestamp": rep["release_timestamp"],
                               "open_interest": rep["open_interest"], "categories": cats})
        return camelize({"instrument": s, "report_type": rt, "primary_category": m.primary_category,
                         "contract": {"name": m.cftc_contract_name, "code": m.cftc_contract_code, "exchange": m.exchange,
                                      "mapping_status": m.mapping_status},
                         "categories": [{"key": c, "label": CATEGORY_LABELS.get(c, c)} for c in CATEGORY_FIELDS[rt]],
                         "source": "CFTC Commitments of Traders", "series": series})

    @r.get("/analysis/{instrument}", summary="Latest institutional intelligence report")
    def analysis(instrument: str, as_of: datetime | None = Query(None, alias="asOf",
                                                                 description="UTC instant: only data public then")):
        require_schema()
        from aurum.cot.intelligence import instrument_report
        if as_of is not None and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        return camelize(instrument_report(repo, cfg, check_symbol(instrument), ids(), price_loader(), as_of=as_of))

    @r.get("/divergence/{instrument}", summary="Price vs positioning divergence (Module 01 prices)")
    def divergence(instrument: str, report_type: ReportType | None = Query(None, alias="reportType"),
                   category: str | None = Query(None, max_length=32),
                   as_of: datetime | None = Query(None, alias="asOf")):
        require_schema()
        from aurum.cot import divergence as dv
        s = check_symbol(instrument)
        mid, rt = mapping_id(s, report_type)
        m = cfg.mappings_for(s, rt)[0]
        cat = category or m.primary_category
        if cat not in CATEGORY_FIELDS[rt]:
            raise HTTPException(422, f"Category '{cat}' does not exist in the {rt} report")
        if as_of is not None and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        met = repo.metrics_frame(mapping_id=mid, category=cat)
        prim = met[["report_date", "net_position", "release_timestamp"]] if not met.empty else met
        res = dv.analyse(prim, price_loader()(s, cfg.analytics.divergence.price_timeframe),
                         cfg.analytics.divergence, as_of=as_of)
        return camelize({"instrument": s, "report_type": rt, "category": cat,
                         "category_label": CATEGORY_LABELS.get(cat, cat), "available": res.available,
                         "reason": res.reason, "window_reports": res.window_reports,
                         "price_timeframe": cfg.analytics.divergence.price_timeframe,
                         "price_coverage": {"from": res.price_coverage[0], "to": res.price_coverage[1]},
                         "series": res.rows, "signals": res.signals, "latest": res.latest,
                         "method": "Price = last completed D1 close on/before each Tuesday report date; signals over "
                                   f"{res.window_reports} report intervals. Not a reversal prediction."})

    # ------------------------------------------------------------------ quality / logs / jobs
    @r.get("/quality", summary="Detected data-quality issues")
    def quality(severity: Severity | None = None, instrument: str | None = None,
                include_resolved: bool = Query(False, alias="includeResolved"),
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500, alias="pageSize")):
        require_schema()
        rows, total = repo.issues(severity=severity, symbol=check_symbol(instrument) if instrument else None,
                                  include_resolved=include_resolved, limit=page_size, offset=(page - 1) * page_size)
        return _page(rows, total, page, page_size)

    @r.get("/logs", summary="Ingestion history")
    def logs(job_type: Literal["backfill", "update", "calculate"] | None = Query(None, alias="jobType"),
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500, alias="pageSize")):
        require_schema()
        rows, total = repo.logs(limit=page_size, offset=(page - 1) * page_size, job_type=job_type)
        return _page(rows, total, page, page_size)

    @r.get("/export/{kind}", summary="Download a CSV export")
    def export(kind: Literal["raw", "positions", "metrics", "analysis", "quality"], instrument: str | None = None):
        require_schema()
        from aurum.cot import exports
        from aurum.cot.intelligence import instrument_report
        sym = check_symbol(instrument) if instrument else None
        syms = [sym] if sym else cfg.symbols
        out_dir = Path(state.settings.resolve(state.settings.data_dir)) / "exports" / "cot" / "api"
        path, _ = exports.export(kind, repo, cfg, out_dir, sym, overwrite=True,
                                 analysis_builder=lambda: [instrument_report(repo, cfg, s_, ids(), price_loader())
                                                           for s_ in syms])
        return FileResponse(path, media_type="text/csv", filename=path.name)

    @r.get("/jobs", summary="Recent background jobs (in-memory)")
    def jobs():
        return camelize([j.public() for j in state.jobs.list()])

    @r.get("/jobs/{job_id}", summary="One background job")
    def job(job_id: str):
        j = state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Job not found (jobs are kept in memory until the API restarts)")
        return camelize(j.public())

    def _summary(job) -> dict[str, Any]:
        return {"job_id": job.job_id, "status": job.status, "totals": job.totals(),
                "metrics_written": job.metrics_written, "fallback_reason": job.fallback_reason,
                "chunks": [c.__dict__ for c in job.chunks]}

    @r.post("/backfill", status_code=202, dependencies=[AdminDep], summary="Start a historical backfill job")
    def backfill(body: BackfillRequest):
        require_schema()
        j = state.jobs.submit("backfill", body.model_dump(mode="json"), lambda progress: _summary(svc.backfill(
            years=body.years, start=body.start, end=body.end, report_types=body.report_types, resume=body.resume,
            progress=progress)))
        return camelize(j.public())

    @r.post("/update", status_code=202, dependencies=[AdminDep], summary="Start an incremental update job")
    def update(body: UpdateRequest | None = None):
        require_schema()
        rts = body.report_types if body else None
        j = state.jobs.submit("update", {"report_types": rts},
                              lambda progress: _summary(svc.update(report_types=rts, progress=progress)))
        return camelize(j.public())

    @r.post("/calculate", status_code=202, dependencies=[AdminDep], summary="Start a metric recalculation job")
    def calculate(body: CalculateRequest | None = None):
        require_schema()
        syms = body.instruments if body else None
        if syms:
            for s_ in syms:
                check_symbol(s_)
        j = state.jobs.submit("calculate", {"instruments": syms},
                              lambda progress: _summary(svc.calculate(symbols=syms, progress=progress)))
        return camelize(j.public())

    return r
