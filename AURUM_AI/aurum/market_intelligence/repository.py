"""Persistence for Module 04. Every write is an idempotent upsert on a natural key; nothing is ever deleted.

* Levels / liquidity events carry ``last_as_of``; an upsert only overwrites a row when the new run's as_of is not
  older (``WHERE stored.last_as_of <= new.last_as_of``), so re-analysing the past never rolls back newer state.
* Calendar events: one row per (provider, provider_event_id) holding the latest version, plus an append-only
  revision log (``economic_calendar_event_revisions``) with a full snapshot of every version AURUM observed. Historical
  queries rebuild each event from the last snapshot observed at or before ``as_of``.
* Policy rates: first observation wins; a later different value for the same date is recorded as a quality issue
  instead of silently overwriting history.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import and_, func, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from aurum.database.connection import session_scope
from aurum.market_intelligence import db_models as M
from aurum.market_intelligence.config import MODULE_VERSION, MIConfig
from aurum.market_intelligence.economic_calendar import VALUE_FIELDS, EconomicEvent, visible_event

log = logging.getLogger(__name__)
UTC = timezone.utc
TABLES = [t.__tablename__ for t in (M.MiRun, M.MiSessionDefinition, M.MiSession, M.MiSessionLevel,
                                    M.MiSessionStatistic, M.MiLiquidityEvent, M.CalendarSource, M.CalendarEvent,
                                    M.CalendarRevision, M.CalendarIngestionLog, M.MacroObservation, M.CentralBankRate,
                                    M.CentralBankDecision, M.MarketNews, M.MiVolatilityMetric, M.MiConditionSnapshot,
                                    M.MiScore, M.MiQualityIssue)]


def _now() -> datetime:
    return datetime.now(UTC)


def _json(v: Any) -> Any:
    return json.loads(json.dumps(v, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)))


def _utc(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class MiRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.dialect = engine.dialect.name

    def _ins(self, table):
        return pg_insert(table) if self.dialect == "postgresql" else sqlite_insert(table)

    def schema_ready(self) -> bool:
        try:
            names = set(inspect(self.engine).get_table_names())
        except Exception:  # noqa: BLE001
            return False
        return set(TABLES) <= names

    def missing_tables(self) -> list[str]:
        names = set(inspect(self.engine).get_table_names())
        return [t for t in TABLES if t not in names]

    # ------------------------------------------------------------------------------------------ runs
    def create_run(self, cfg: MIConfig, run_type: str, symbol: str | None, as_of: datetime, status: str = "ok",
                   summary: dict[str, Any] | None = None) -> str:
        uid = str(uuid.uuid4())
        with session_scope(self.engine) as s:
            s.add(M.MiRun(run_uid=uid, run_type=run_type, symbol=symbol, as_of=as_of, generated_at=_now(),
                          module_version=MODULE_VERSION, methodology_version=cfg.methodology_version,
                          config_fingerprint=cfg.fingerprint, status=status, summary=_json(summary)))
        return uid

    def runs(self, symbol: str | None = None, run_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = select(M.MiRun).order_by(M.MiRun.created_at.desc()).limit(limit)
        if symbol:
            q = q.where(M.MiRun.symbol == symbol)
        if run_type:
            q = q.where(M.MiRun.run_type == run_type)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.MiRun.__table__.columns} for r in s.execute(q).scalars()]

    # ------------------------------------------------------------------------------------------ sessions
    def save_definitions(self, cfg: MIConfig) -> int:
        rows = []
        for w in cfg.windows().values():
            d = w.describe()
            rows.append({"name": w.name, "kind": w.kind, "timezone": w.timezone, "local_open": d["open"],
                         "local_close": d["close"], "weekdays": ",".join(d["weekdays"]), "holidays": d["holidays"],
                         "pre_minutes": w.pre_minutes, "post_minutes": w.post_minutes,
                         "config_fingerprint": cfg.fingerprint, "updated_at": _now()})
        with session_scope(self.engine) as s:
            for r in rows:
                st = self._ins(M.MiSessionDefinition).values(**r)
                st = st.on_conflict_do_update(index_elements=["name", "kind"],
                                              set_={k: st.excluded[k] for k in r if k not in ("name", "kind")})
                s.execute(st)
        return len(rows)

    def save_sessions(self, intervals: Iterable[Any]) -> int:
        n = 0
        with session_scope(self.engine) as s:
            for i in intervals:
                st = self._ins(M.MiSession).values(name=i.name, kind=i.kind, start_utc=i.start, end_utc=i.end,
                                                   local_date=i.local_date, timezone=i.timezone, created_at=_now())
                s.execute(st.on_conflict_do_nothing(index_elements=["name", "kind", "start_utc"]))
                n += 1
        return n

    # ------------------------------------------------------------------------------------------ levels / liquidity
    def save_levels(self, run_uid: str, as_of: datetime, levels: list[dict[str, Any]]) -> int:
        cols = ("price", "period_label", "period_end", "formed_at", "source_candle_time", "timeframe", "method",
                "provisional", "state", "swept_at", "broken_at", "candles", "missing_candles", "detail",
                "last_run_uid", "last_as_of", "updated_at")
        with session_scope(self.engine) as s:
            for lv in levels:
                row = {"symbol": lv["symbol"], "level_type": lv["level_type"], "side": lv["side"],
                       "price": lv["price"], "period_kind": lv["period"], "period_label": lv.get("period_label"),
                       "period_start": lv["period_start"], "period_end": lv["period_end"],
                       "formed_at": lv["formed_at"], "source_candle_time": lv["source_candle_time"],
                       "timeframe": lv["timeframe"], "method": lv["method"], "provisional": lv["provisional"],
                       "state": lv["state"], "swept_at": lv.get("swept_at"), "broken_at": lv.get("broken_at"),
                       "candles": lv.get("candles"), "missing_candles": lv.get("missing_candles"),
                       "detail": _json({k: lv.get(k) for k in ("note", "sweep_candle_time", "break_candle_time",
                                                               "checked_until", "expected_candles")}),
                       "first_run_uid": run_uid, "last_run_uid": run_uid, "last_as_of": as_of, "updated_at": _now()}
                st = self._ins(M.MiSessionLevel).values(**row)
                st = st.on_conflict_do_update(index_elements=["symbol", "level_type", "period_start"],
                                              set_={c: st.excluded[c] for c in cols},
                                              where=M.MiSessionLevel.last_as_of <= st.excluded.last_as_of)
                s.execute(st)
        return len(levels)

    def save_liquidity(self, run_uid: str, as_of: datetime, events: list[dict[str, Any]]) -> int:
        cols = ("level", "event_type", "direction", "confirmation_time", "source_candle_time", "timeframe",
                "closed_beyond", "returned_inside", "returned_at", "related_structure", "detail", "last_run_uid",
                "last_as_of", "updated_at")
        with session_scope(self.engine) as s:
            for e in events:
                row = {"symbol": e["symbol"], "session": e.get("session") or "", "level_type": e["level_type"],
                       "level": e.get("level"), "event_type": e["event_type"], "direction": e.get("direction"),
                       "event_time": e["event_time"], "confirmation_time": e.get("confirmation_time"),
                       "source_candle_time": e["source_candle_time"], "timeframe": e["timeframe"],
                       "closed_beyond": e.get("closed_beyond"), "returned_inside": e.get("returned_inside"),
                       "returned_at": e.get("returned_at"), "related_structure": _json(e.get("related_structure")),
                       "detail": _json({k: e.get(k) for k in ("extreme", "close", "rule", "ratio", "mean_range",
                                                              "range", "samples", "side")}),
                       "first_run_uid": run_uid, "last_run_uid": run_uid, "last_as_of": as_of, "updated_at": _now()}
                st = self._ins(M.MiLiquidityEvent).values(**row)
                st = st.on_conflict_do_update(index_elements=["symbol", "session", "level_type", "event_time"],
                                              set_={c: st.excluded[c] for c in cols},
                                              where=M.MiLiquidityEvent.last_as_of <= st.excluded.last_as_of)
                s.execute(st)
        return len(events)

    def levels(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        q = (select(M.MiSessionLevel).where(M.MiSessionLevel.symbol == symbol)
             .order_by(M.MiSessionLevel.period_end.desc()).limit(limit))
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.MiSessionLevel.__table__.columns} for r in s.execute(q).scalars()]

    def liquidity_events(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        q = (select(M.MiLiquidityEvent).where(M.MiLiquidityEvent.symbol == symbol)
             .order_by(M.MiLiquidityEvent.event_time.desc()).limit(limit))
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.MiLiquidityEvent.__table__.columns}
                    for r in s.execute(q).scalars()]

    # ------------------------------------------------------------------------------------------ snapshots
    def _replace(self, table, keys: list[str], row: dict[str, Any]) -> None:
        with session_scope(self.engine) as s:
            st = self._ins(table).values(**row)
            st = st.on_conflict_do_update(index_elements=keys, set_={k: st.excluded[k] for k in row if k not in keys})
            s.execute(st)

    def save_statistics(self, run_uid: str, symbol: str, as_of: datetime, stats: dict[str, Any]) -> int:
        n = 0
        for name, s in (stats.get("sessions") or {}).items():
            for w, m in s["windows"].items():
                self._replace(M.MiSessionStatistic, ["symbol", "session", "window", "as_of"],
                              {"run_uid": run_uid, "symbol": symbol, "session": name, "window": int(w), "as_of": as_of,
                               "sample_size": m["sample_size"], "sufficient": m["sufficient"], "metrics": _json(m)})
                n += 1
        return n

    def save_volatility(self, run_uid: str, symbol: str, as_of: datetime, vol: dict[str, Any]) -> int:
        for tf, v in vol["timeframes"].items():
            self._replace(M.MiVolatilityMetric, ["symbol", "timeframe", "as_of"],
                          {"run_uid": run_uid, "symbol": symbol, "timeframe": tf, "as_of": as_of,
                           "regime": v.get("regime", "UNKNOWN"), "atr": v.get("atr"),
                           "atr_percentile": v.get("atr_percentile"), "realised_vol_pct": v.get("realised_vol_pct"),
                           "metrics": _json(v)})
        return len(vol["timeframes"])

    def save_conditions(self, run_uid: str, symbol: str, as_of: datetime, cond: dict[str, Any]) -> None:
        self._replace(M.MiConditionSnapshot, ["symbol", "as_of"],
                      {"run_uid": run_uid, "symbol": symbol, "as_of": as_of, "tags": cond["tags"],
                       "conditions": _json(cond["conditions"]), "summary": cond["summary"]})

    def save_scores(self, run_uid: str, symbol: str, as_of: datetime, cfg: MIConfig, scores: dict[str, Any]) -> int:
        n = 0
        for kind in ("event_risk", "session_activity", "volatility"):
            sc = scores.get(kind)
            if sc is None:
                continue
            self._replace(M.MiScore, ["symbol", "as_of", "score_type"],
                          {"run_uid": run_uid, "symbol": symbol, "as_of": as_of, "score_type": kind,
                           "score": sc.get("score"), "methodology_version": cfg.methodology_version,
                           "components": _json(sc)})
            n += 1
        return n

    def record_issues(self, run_uid: str | None, symbol: str | None, component: str,
                      issues: Iterable[dict[str, Any]]) -> int:
        n = 0
        with session_scope(self.engine) as s:
            for i in issues:
                kind = str(i.get("issue") or i.get("issue_type") or "issue")
                fp = hashlib.sha256(json.dumps([symbol, component, kind, i.get("level"), str(i.get("period_start")),
                                                i.get("feed"), i.get("provider")], default=str).encode()).hexdigest()
                st = self._ins(M.MiQualityIssue).values(
                    fingerprint=fp, run_uid=run_uid, symbol=symbol, component=component, issue_type=kind,
                    severity=str(i.get("severity") or "warning"), message=i.get("message") or i.get("error"),
                    details=_json(i), first_seen_at=_now(), last_seen_at=_now(), occurrences=1)
                st = st.on_conflict_do_update(index_elements=["fingerprint"], set_={
                    "last_seen_at": st.excluded.last_seen_at, "run_uid": st.excluded.run_uid,
                    "occurrences": M.MiQualityIssue.occurrences + 1})
                s.execute(st)
                n += 1
        return n

    def quality_issues(self, component: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = select(M.MiQualityIssue).order_by(M.MiQualityIssue.last_seen_at.desc()).limit(limit)
        if component:
            q = q.where(M.MiQualityIssue.component == component)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.MiQualityIssue.__table__.columns} for r in s.execute(q).scalars()]

    # ------------------------------------------------------------------------------------------ calendar
    @staticmethod
    def _snapshot(ev: EconomicEvent) -> dict[str, Any]:
        d = ev.to_dict()
        d.pop("retrieved_at", None)
        return _json(d)

    def upsert_events(self, events: list[EconomicEvent], run_uid: str) -> dict[str, int]:
        stats = {"inserted": 0, "updated": 0, "unchanged": 0}
        now = _now()
        with session_scope(self.engine) as s:
            for ev in events:
                cur = s.execute(select(M.CalendarEvent).where(M.CalendarEvent.provider == ev.provider,
                                                              M.CalendarEvent.provider_event_id == ev.provider_event_id)
                                ).scalar_one_or_none()
                values = {k: getattr(ev, k) for k in VALUE_FIELDS}
                snap = self._snapshot(ev)
                if cur is None:
                    row = M.CalendarEvent(
                        provider=ev.provider, provider_event_id=ev.provider_event_id, name=ev.name, country=ev.country,
                        currency=ev.currency, category=ev.category, subcategory=ev.subcategory,
                        importance=ev.importance, importance_source=ev.importance_source, scheduled_at=ev.scheduled_at,
                        original_scheduled_at=ev.scheduled_at, source_timezone=ev.source_timezone,
                        time_precision=ev.time_precision, values=_json(values), unit=ev.unit, status=ev.status,
                        source_url=ev.source_url, provider_updated_at=ev.provider_updated_at,
                        first_retrieved_at=ev.retrieved_at or now, retrieved_at=ev.retrieved_at or now,
                        actual_first_seen_at=now if ev.actual is not None else None,
                        affected_instruments=_json(ev.affected_instruments), raw=_json(ev.raw), created_at=now,
                        updated_at=now)
                    s.add(row)
                    s.flush()
                    s.add(M.CalendarRevision(event_id=row.id, observed_at=now, change_type="created", changes=None,
                                             snapshot=snap, ingestion_run_uid=run_uid))
                    stats["inserted"] += 1
                    continue
                changes: dict[str, list[Any]] = {}
                old_vals = cur.values or {}
                for k in VALUE_FIELDS:
                    if _json(old_vals.get(k)) != _json(values[k]):
                        changes[k] = [old_vals.get(k), _json(values[k])]
                for k in ("name", "importance", "category", "unit", "currency", "time_precision"):
                    if getattr(cur, k) != getattr(ev, k):
                        changes[k] = [getattr(cur, k), getattr(ev, k)]
                if _utc(cur.scheduled_at) != ev.scheduled_at:
                    changes["scheduled_at"] = [cur.scheduled_at.isoformat(), ev.scheduled_at.isoformat()]
                status = ev.status
                if "scheduled_at" in changes and ev.scheduled_at > _utc(cur.scheduled_at) and ev.actual is None:
                    status = "delayed" if ev.status not in ("cancelled",) else ev.status
                if old_vals.get("actual") is not None and "actual" in changes:
                    status = "revised"
                if "previous" in changes and old_vals.get("previous") is not None and ev.revised_previous is None:
                    values["revised_previous"] = values["previous"]
                if status != cur.status:
                    changes["status"] = [cur.status, status]
                cur.retrieved_at = ev.retrieved_at or now
                if not changes:
                    stats["unchanged"] += 1
                    continue
                kind = ("rescheduled" if "scheduled_at" in changes else "values" if set(changes) & set(VALUE_FIELDS)
                        else "status" if "status" in changes else "metadata")
                cur.name, cur.importance, cur.importance_source = ev.name, ev.importance, ev.importance_source
                cur.category, cur.subcategory, cur.unit, cur.currency = ev.category, ev.subcategory, ev.unit, ev.currency
                cur.scheduled_at, cur.time_precision, cur.status = ev.scheduled_at, ev.time_precision, status
                cur.values = _json(values)
                cur.provider_updated_at, cur.source_url = ev.provider_updated_at, ev.source_url or cur.source_url
                cur.affected_instruments = _json(ev.affected_instruments)
                cur.raw, cur.updated_at = _json(ev.raw), now
                if cur.actual_first_seen_at is None and ev.actual is not None:
                    cur.actual_first_seen_at = now
                snap.update({"status": status, **_json(values)})
                s.add(M.CalendarRevision(event_id=cur.id, observed_at=now, change_type=kind, changes=_json(changes),
                                         snapshot=snap, ingestion_run_uid=run_uid))
                stats["updated"] += 1
        return stats

    def _event_dict(self, r: M.CalendarEvent) -> dict[str, Any]:
        v = r.values or {}
        return {"id": r.id, "event_uid": f"{r.provider}:{r.provider_event_id}", "provider": r.provider,
                "provider_event_id": r.provider_event_id, "name": r.name, "country": r.country,
                "currency": r.currency, "category": r.category, "subcategory": r.subcategory,
                "importance": r.importance, "importance_source": r.importance_source,
                "scheduled_at": _utc(r.scheduled_at), "original_scheduled_at": _utc(r.original_scheduled_at),
                "source_timezone": r.source_timezone, "time_precision": r.time_precision,
                **{k: v.get(k) for k in VALUE_FIELDS}, "unit": r.unit, "status": r.status,
                "source_url": r.source_url, "provider_updated_at": r.provider_updated_at,
                "first_retrieved_at": r.first_retrieved_at, "retrieved_at": r.retrieved_at,
                "actual_first_seen_at": r.actual_first_seen_at, "affected_instruments": r.affected_instruments or [],
                "quality_status": r.quality_status}

    def events(self, start: datetime | None = None, end: datetime | None = None, *, as_of: datetime | None = None,
               currencies: list[str] | None = None, importance: list[str] | None = None,
               categories: list[str] | None = None, providers: list[str] | None = None,
               instrument: str | None = None, offset: int = 0, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
        """Events in [start, end). With ``as_of`` each event is rebuilt from the last version AURUM had observed by
        then (events first seen later are excluded) and its actual is hidden until its release time."""
        q = select(M.CalendarEvent).order_by(M.CalendarEvent.scheduled_at, M.CalendarEvent.id)
        if start is not None:
            q = q.where(M.CalendarEvent.scheduled_at >= start)
        if end is not None:
            q = q.where(M.CalendarEvent.scheduled_at < end)
        if currencies:
            q = q.where(M.CalendarEvent.currency.in_([c.upper() for c in currencies]))
        if importance:
            q = q.where(M.CalendarEvent.importance.in_([i.lower() for i in importance]))
        if categories:
            q = q.where(M.CalendarEvent.category.in_(categories))
        if providers:
            q = q.where(M.CalendarEvent.provider.in_(providers))
        with session_scope(self.engine) as s:
            rows = list(s.execute(q).scalars())
            out = []
            if as_of is None:
                out = [self._event_dict(r) for r in rows]
            else:
                ids = [r.id for r in rows]
                revs: dict[int, M.CalendarRevision] = {}
                if ids:
                    for rv in s.execute(select(M.CalendarRevision).where(M.CalendarRevision.event_id.in_(ids),
                                                                         M.CalendarRevision.observed_at <= as_of)
                                        .order_by(M.CalendarRevision.observed_at)).scalars():
                        revs[rv.event_id] = rv
                for r in rows:
                    rv = revs.get(r.id)
                    if rv is None:
                        continue                       # AURUM had not seen this event yet at as_of
                    d = self._event_dict(r)
                    snap = rv.snapshot
                    d.update({k: snap.get(k) for k in (*VALUE_FIELDS, "importance", "status", "name", "category",
                                                       "time_precision")})
                    d["scheduled_at"] = _utc(snap.get("scheduled_at")) or d["scheduled_at"]
                    d["affected_instruments"] = snap.get("affected_instruments") or d["affected_instruments"]
                    d["version_observed_at"] = rv.observed_at
                    out.append(visible_event(d, as_of))
        if instrument:
            out = [e for e in out if any(a["instrument"] == instrument for a in e.get("affected_instruments") or [])]
        total = len(out)
        if limit is not None:
            out = out[offset:offset + limit]
        return out, total

    def event(self, event_id: int) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            r = s.get(M.CalendarEvent, event_id)
            if r is None:
                return None
            d = self._event_dict(r)
            d["revisions"] = [{"observed_at": rv.observed_at, "change_type": rv.change_type, "changes": rv.changes}
                              for rv in s.execute(select(M.CalendarRevision).where(M.CalendarRevision.event_id == r.id)
                                                  .order_by(M.CalendarRevision.observed_at)).scalars()]
            d["raw"] = r.raw
            return d

    def log_ingestion(self, **fields: Any) -> None:
        with session_scope(self.engine) as s:
            s.add(M.CalendarIngestionLog(**fields))

    def ingestion_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        q = select(M.CalendarIngestionLog).order_by(M.CalendarIngestionLog.started_at.desc()).limit(limit)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.CalendarIngestionLog.__table__.columns}
                    for r in s.execute(q).scalars()]

    def update_source(self, provider: str, **fields: Any) -> None:
        row = {"provider": provider, "updated_at": _now(), **fields}
        with session_scope(self.engine) as s:
            st = self._ins(M.CalendarSource).values(**row)
            s.execute(st.on_conflict_do_update(index_elements=["provider"],
                                               set_={k: st.excluded[k] for k in row if k != "provider"}))

    def sources(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.CalendarSource.__table__.columns}
                    for r in s.execute(select(M.CalendarSource)).scalars()]

    # ------------------------------------------------------------------------------------------ macro
    def save_rates(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        out = {"inserted": 0, "unchanged": 0, "conflicts": 0}
        conflicts = []
        with session_scope(self.engine) as s:
            for r in rows:
                cur = s.execute(select(M.CentralBankRate).where(M.CentralBankRate.bank == r["bank"],
                                                                M.CentralBankRate.effective_date == r["effective_date"])
                                ).scalar_one_or_none()
                if cur is None:
                    s.add(M.CentralBankRate(**r))
                    out["inserted"] += 1
                elif cur.rate != r["rate"] or cur.rate_lower != r.get("rate_lower"):
                    out["conflicts"] += 1
                    conflicts.append({"issue": "rate_revision", "provider": r["bank"], "severity": "warning",
                                      "message": f"{r['bank']} {r['effective_date']}: stored {cur.rate}, source now "
                                                 f"{r['rate']} - stored history kept.", "level": str(r["effective_date"])})
                else:
                    out["unchanged"] += 1
        if conflicts:
            self.record_issues(None, None, "macro", conflicts)
        return out

    def save_decisions(self, rows: list[dict[str, Any]]) -> int:
        with session_scope(self.engine) as s:
            for r in rows:
                st = self._ins(M.CentralBankDecision).values(**r)
                s.execute(st.on_conflict_do_update(index_elements=["bank", "decision_date"],
                                                   set_={k: st.excluded[k] for k in r if k not in ("bank", "decision_date")}))
        return len(rows)

    def rates(self, bank: str, as_of: datetime | None = None, limit: int = 2) -> list[dict[str, Any]]:
        q = select(M.CentralBankRate).where(M.CentralBankRate.bank == bank)
        if as_of is not None:
            q = q.where(M.CentralBankRate.known_from <= as_of)
        q = q.order_by(M.CentralBankRate.effective_date.desc()).limit(limit)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.CentralBankRate.__table__.columns}
                    for r in s.execute(q).scalars()]

    def rate_changes(self, bank: str, as_of: datetime | None = None, limit: int = 2) -> list[dict[str, Any]]:
        """Last ``limit`` stored rates where the value differs from the previous stored rate."""
        q = select(M.CentralBankRate).where(M.CentralBankRate.bank == bank)
        if as_of is not None:
            q = q.where(M.CentralBankRate.known_from <= as_of)
        with session_scope(self.engine) as s:
            rows = list(s.execute(q.order_by(M.CentralBankRate.effective_date)).scalars())
        chg, prev = [], None
        for r in rows:
            if prev is None or r.rate != prev.rate or r.rate_lower != prev.rate_lower:
                chg.append({c.name: getattr(r, c.name) for c in M.CentralBankRate.__table__.columns})
            prev = r
        return chg[-limit:]

    def decisions(self, bank: str, as_of: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
        q = select(M.CentralBankDecision).where(M.CentralBankDecision.bank == bank)
        if as_of is not None:
            q = q.where(M.CentralBankDecision.known_from <= as_of)
        q = q.order_by(M.CentralBankDecision.decision_date.desc()).limit(limit)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in M.CentralBankDecision.__table__.columns}
                    for r in s.execute(q).scalars()]

    def latest_rate_retrieval(self, bank: str) -> datetime | None:
        with session_scope(self.engine) as s:
            v = s.execute(select(func.max(M.CentralBankRate.retrieved_at)).where(M.CentralBankRate.bank == bank)).scalar()
        return _utc(v)

    def save_observations(self, meta: dict[str, Any], rows: list[dict[str, Any]], retrieved_at: datetime,
                          known_from_fn=None) -> dict[str, int]:
        """Vintage rows (``vintage_date`` set) are inserted once per vintage. Rows without vintages are inserted only
        when the value for that period differs from the latest stored one, known from the retrieval time."""
        out = {"inserted": 0, "unchanged": 0}
        with session_scope(self.engine) as s:
            latest: dict[str, float] = {}
            if any(r.get("vintage_date") is None for r in rows):
                sub = (select(M.MacroObservation.period, func.max(M.MacroObservation.known_from).label("k"))
                       .where(M.MacroObservation.series_id == meta["id"]).group_by(M.MacroObservation.period).subquery())
                q = select(M.MacroObservation.period, M.MacroObservation.value).join(
                    sub, and_(sub.c.period == M.MacroObservation.period, sub.c.k == M.MacroObservation.known_from)
                ).where(M.MacroObservation.series_id == meta["id"])
                latest = {p: v for p, v in s.execute(q)}
            for r in rows:
                if r.get("vintage_date") is None:
                    if latest.get(r["period"]) == r["value"]:
                        out["unchanged"] += 1
                        continue
                    vintage, kf = f"r:{retrieved_at.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}", retrieved_at
                else:
                    vintage, kf = str(r["vintage_date"]), known_from_fn(r["vintage_date"])
                st = self._ins(M.MacroObservation).values(
                    series_id=meta["id"], source=meta["source"], name=meta["name"], category=meta.get("category"),
                    currency=meta.get("currency"), unit=meta.get("unit"), period=r["period"], value=r["value"],
                    vintage=vintage, known_from=kf, retrieved_at=retrieved_at, source_url=r.get("source_url"))
                res = s.execute(st.on_conflict_do_nothing(index_elements=["series_id", "period", "vintage"]))
                out["inserted" if res.rowcount else "unchanged"] += 1
        return out

    def observations(self, series_id: str, as_of: datetime | None = None, limit: int = 13) -> list[dict[str, Any]]:
        """Point-in-time series: for each period the latest value known at ``as_of`` (newest periods first)."""
        q = select(M.MacroObservation).where(M.MacroObservation.series_id == series_id)
        if as_of is not None:
            q = q.where(M.MacroObservation.known_from <= as_of)
        with session_scope(self.engine) as s:
            rows = list(s.execute(q.order_by(M.MacroObservation.known_from)).scalars())
        by_period: dict[str, M.MacroObservation] = {}
        for r in rows:
            by_period[r.period] = r
        ordered = sorted(by_period.values(), key=lambda r: r.period, reverse=True)[:limit]
        return [{c.name: getattr(r, c.name) for c in M.MacroObservation.__table__.columns} for r in ordered]

    # ------------------------------------------------------------------------------------------ news
    def save_news(self, items: list[dict[str, Any]]) -> dict[str, int]:
        out = {"inserted": 0, "unchanged": 0}
        with session_scope(self.engine) as s:
            for i in items:
                st = self._ins(M.MarketNews).values(
                    item_uid=i["item_uid"], provider=i["provider"], source=i["source"], headline=i["headline"],
                    url=i["url"], published_at=i.get("published_at"), retrieved_at=i["retrieved_at"],
                    category=i.get("category"), source_type=i["source_type"], currencies=i.get("currencies"),
                    reliability=i.get("reliability"), description=i.get("description"))
                res = s.execute(st.on_conflict_do_nothing(index_elements=["item_uid"]))
                out["inserted" if res.rowcount else "unchanged"] += 1
        return out

    def news(self, as_of: datetime | None = None, currencies: list[str] | None = None, limit: int = 50,
             offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        q = select(M.MarketNews).order_by(M.MarketNews.published_at.desc())
        if as_of is not None:
            q = q.where(M.MarketNews.published_at <= as_of)
        with session_scope(self.engine) as s:
            rows = [{c.name: getattr(r, c.name) for c in M.MarketNews.__table__.columns} for r in s.execute(q).scalars()]
        if currencies:
            want = {c.upper() for c in currencies}
            rows = [r for r in rows if want & set(r.get("currencies") or [])]
        return rows[offset:offset + limit], len(rows)

    def table_counts(self) -> dict[str, int]:
        out = {}
        with self.engine.connect() as c:
            from sqlalchemy import text
            for t in TABLES:
                try:
                    out[t] = int(c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0)
                except Exception:  # noqa: BLE001
                    out[t] = -1
        return out
