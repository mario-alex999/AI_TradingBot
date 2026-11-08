"""DecisionService: runs the pipeline, serves candidates and records human decisions.

Pipeline: context -> templates -> score -> gates -> publish -> AI review. Nothing here can reach a broker. Approving a
candidate records a person's decision and sends nothing to MT5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy.engine import Engine

from aurum.ai_review import budget
from aurum.config import Settings, get_settings

from . import DISCLAIMER, ENGINE_VERSION, frontend_views, lifecycle
from .adapters import AccountAdapter, CotPositioning, IntelligenceAdapter, LearningAdapter, NullAccount, NullLearning, SpreadSource, utc
from .ai_reviewer.client import AiReviewer
from .ai_reviewer.prompt import build_payload
from .ai_reviewer.providers import providers as provider_list
from .ai_reviewer.schema import final_confidence
from .backtest.cache import ReplayTechnical
from .backtest.metrics import SimTrade, report
from .backtest.replay import ReplayResult, candidates_at, replay_symbol
from .candidates.setups import Candidate, generate
from .candidates.sizing import suggest_volume
from .config import DecisionConfig, load_decision_config, with_overrides
from .context import ContextBuilder, DecisionContext
from .gates import GateResult, confidence_cap, first_failure, run_gates
from .outcomes import label
from .recommendation import Assessed, decide, evidence_summary, explain, find_conflicts
from .regime import assess_bias, assess_regime
from .repository import DecisionRepository
from .scoring import Score, calibration

log = logging.getLogger(__name__)
GRADE_ORDER = {"A": 3, "B": 2, "C": 1, "D": 0, "F": -1}


class DecisionError(RuntimeError):
    """Base class. ``str(exc)`` is safe to show to the user."""


class SchemaNotReady(DecisionError):
    pass


class CandidateNotFound(DecisionError):
    pass


class ApprovalBlocked(DecisionError):
    """The candidate cannot be approved right now; the message says why."""


def _ai_hash(c: Candidate, gates: list[GateResult], s: Score, ctx: DecisionContext) -> str:
    """What the reviewer's opinion depends on. New candles alone do not change it, so a review is reused until a number,
    a gate, the score or the event and volatility state actually moves."""
    state = {"levels": [c.entry_low, c.entry_high, c.stop_loss, c.take_profit, c.take_profit2], "gates": [(g.gate, g.result) for g in gates],
             "score": s.confidence, "event": ctx.event_state, "regime": ctx.regime}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


class DecisionService:
    def __init__(self, settings: Settings | None = None, engine: Engine | None = None, cfg: DecisionConfig | None = None,
                 technical: Any = None, intelligence: Any = None, builder: ContextBuilder | None = None, ai_client: Any = None,
                 now: Callable[[], datetime] | None = None):
        from aurum.database.connection import engine_from_settings
        self.settings = settings or get_settings()
        self.engine = engine or engine_from_settings(self.settings)
        self.cfg = cfg or with_overrides(load_decision_config(), self._saved_overrides())
        self.repo = DecisionRepository(self.engine)
        self.ai = AiReviewer(self.settings, self.engine, self.cfg, ai_client)
        self.now = now or (lambda: datetime.now(timezone.utc).replace(microsecond=0))
        self._technical, self._intelligence = technical, intelligence
        self._builder = builder

    # ------------------------------------------------------------------------------------------- wiring
    @property
    def technical(self) -> Any:
        if self._technical is None:
            from aurum.analysis.services.analysis_service import TechnicalAnalysisService
            self._technical = TechnicalAnalysisService(self.settings, self.engine)
        return self._technical

    @property
    def intelligence(self) -> Any:
        if self._intelligence is None:
            from aurum.market_intelligence.service import MarketIntelligenceService
            self._intelligence = MarketIntelligenceService(self.settings, self.engine)
        return self._intelligence

    def _specs(self) -> Callable[[str], dict[str, Any] | None]:
        rows = {r["symbol"]: r for r in self.technical.candles.latest_specs()}
        return rows.get

    @property
    def builder(self) -> ContextBuilder:
        if self._builder is None:
            lineage = Path(self.settings.resolve(self.settings.data_dir)) / "learning" / "L1.json"
            self._builder = ContextBuilder(self.cfg, self.technical, IntelligenceAdapter(self.intelligence),
                                           AccountAdapter(self.engine, self.settings), LearningAdapter(lineage),
                                           SpreadSource(self.technical.candles), self._specs(), CotPositioning(self.engine))
        return self._builder

    def symbols(self) -> list[str]:
        instruments = self.technical.instruments
        return [s for s in self.settings.enabled_instruments if s in instruments] or list(instruments)

    def _require_schema(self) -> None:
        if not self.repo.schema_ready():
            raise SchemaNotReady("Module 05 tables are not initialised: run `python -m aurum.cli init-db`.")

    # ------------------------------------------------------------------------------------------- run
    def run(self, symbol: str, as_of: datetime | None = None, ai: bool = True, timeframes: list[str] | None = None) -> dict[str, Any]:
        self._require_schema()
        started = time.monotonic()
        now = self.now()
        t = utc(as_of) if as_of else now
        self.expire_stale(now)
        ctx = self.builder.build(symbol, t)
        found, skipped = generate(ctx, self.cfg, None, timeframes)
        scored = candidates_at(ctx, self.cfg, None, timeframes) if found else []
        run_uid = self.repo.save_run(symbol=ctx.symbol, as_of=t, mode="live", engine_version=ENGINE_VERSION,
                                     config_fingerprint=self.cfg.fingerprint, input_hash=ctx.input_hash, availability=ctx.availability,
                                     status="ok" if scored else "no_setup",
                                     summary={"candidates": len(scored), "skipped": [s.__dict__ for s in skipped]})
        pending = [{"symbol": a["symbol"], "direction": a["direction"], "key": a["idempotent_key"]} for a in self.repo.active(ctx.symbol)]
        published: list[dict[str, Any]] = []
        assessed: list[Assessed] = []
        for c, s in scored:
            gates = run_gates(ctx, c, self.cfg, pending)
            equity = ctx.account.get("equity")
            limit = (ctx.account.get("limits") or {}).get("max_per_trade_pct")
            volume, why = suggest_volume(self.cfg.sizing["default_risk_pct"], equity, abs(c.entry_mid - c.stop_loss), ctx.spec, limit)
            cap = confidence_cap(gates)
            first = final_confidence(s.confidence, cap, {"status": "unavailable"}, self.cfg.ai_review["unavailable_confidence_cap"])
            row = self.repo.upsert(c, s, gates, volume, why, run_uid, ctx.input_hash, ctx.availability, first, now)
            frozen = row["frozen"]      # a final decision stands, so this run neither reviews nor counts it
            if not frozen and row["status"] in lifecycle.ACTIVE:
                pending.append({"symbol": c.symbol, "direction": c.direction, "key": c.key})
                row = self._review(ctx, c, s, gates, cap, volume, row, ai, now)
            published.append(frontend_views.signal(row, [p.sentence for p in s.components]))
            if not frozen:
                latest = self.ai.audit.latest(row["candidate_uid"]) if row["status"] != "new" else None
                verdict = latest["verdict"] if latest and latest["status"] == "reviewed" else None
                assessed.append(Assessed(c, s, gates, verdict))
        analysis = self._analysis(ctx, assessed, skipped)
        self.repo.update_summary(run_uid, {"candidates": len(scored), "skipped": [s.__dict__ for s in skipped], **analysis,
                                           "signal_ids": [p["id"] for p in published]})
        return {"symbol": ctx.symbol, "as_of": t, "status": "ok" if scored else "no_setup", "run_id": run_uid, "signals": published,
                "skipped": [s.__dict__ for s in skipped], "availability": ctx.availability, "analysis": analysis, "disclaimer": DISCLAIMER}

    def _analysis(self, ctx: DecisionContext, assessed: list[Assessed], skipped: list[Any]) -> dict[str, Any]:
        regime, bias = assess_regime(ctx, self.cfg), assess_bias(ctx, self.cfg)
        conflicts = find_conflicts(ctx, regime, bias, assessed)
        verdict = decide(ctx, self.cfg, regime, bias, assessed, conflicts)
        return {"regime": regime, "bias": bias, "conflicts": conflicts, "decision": verdict, "explanation": explain(ctx, regime, bias, assessed, conflicts, verdict),
                "evidence": [{"template": a.candidate.template, "timeframe": a.candidate.timeframe, "direction": a.candidate.direction,
                              **evidence_summary(a, conflicts)} for a in assessed],
                "cot_positioning": ctx.cot_positioning, "human_review": "PENDING_HUMAN_REVIEW", "requires_human_review": True}

    def run_all(self, symbols: Iterable[str] | None = None, as_of: datetime | None = None, ai: bool = True,
                timeframes: list[str] | None = None) -> list[dict[str, Any]]:
        return [self.run(s, as_of, ai, timeframes) for s in (symbols or self.symbols())]

    def _review(self, ctx: DecisionContext, c: Candidate, s: Score, gates: list[GateResult], cap: float | None, volume: float | None,
                row: dict[str, Any], ai: bool, now: datetime) -> dict[str, Any]:
        """The AI step. It can lower confidence or reject. A failure leaves the candidate published, capped and unpromoted."""
        uid = row["candidate_uid"]
        ai_cap = self.cfg.ai_review["unavailable_confidence_cap"]
        key = _ai_hash(c, gates, s, ctx)
        if not ai:      # a --no-ai run makes no call, but a review already paid for still counts
            result = self.ai.audit.reusable(uid, key, self.ai.prompt_version) or {}
        elif GRADE_ORDER[s.grade] < GRADE_ORDER[self.cfg.ai_review["min_grade"]]:
            result = self.ai.audit.record_unavailable_once(uid, key, self.ai.prompt_version, "below_min_grade")
        else:
            result = self.ai.review(uid, key, build_payload(ctx, c, gates, s, self._template_history(c.template, c.symbol), volume))
        if result.get("status") != "reviewed":
            return row
        confidence = final_confidence(s.confidence, cap, result, ai_cap)
        self.repo.set_confidence(uid, confidence, self.cfg, now)
        overridden = any(h["from"] == "rejected" and h["actor"].startswith("human:") for h in self.repo.history(uid))
        if result.get("verdict") == "reject" and not overridden:     # a person's re-open outranks the same cached verdict
            return self.repo.move(uid, "rejected", "ai_reviewer", (result.get("review") or {}).get("summary"), now, rejected_by="ai_reviewer")
        if row["status"] == "new":
            return self.repo.move(uid, "under_review", "system", "AI review finished.", now)
        return {**row, "confidence": confidence}

    def _template_history(self, template: str, symbol: str) -> dict[str, Any]:
        rows = self.repo.resolved(symbol, template)
        rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
        return {"resolved_trades": len(rs), "hit_rate": round(sum(1 for r in rs if r > 0) / len(rs), 3) if rs else None,
                "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None}

    # ------------------------------------------------------------------------------------------- lifecycle
    def _candidate(self, uid: str) -> dict[str, Any]:
        self._require_schema()
        row = self.repo.get(uid)
        if row is None:
            raise CandidateNotFound(f"Unknown candidate '{uid}'.")
        return row

    @staticmethod
    def _as_candidate(row: dict[str, Any], as_of: datetime) -> Candidate:
        c = Candidate(row["symbol"], row["template"], row["timeframe"], row["direction"], "", as_of, row["entry_low"], row["entry_high"],
                      row["stop_loss"], row["take_profit"], row["take_profit2"], row["risk_reward"], row["expires_at"], row["invalidation"],
                      row["market_condition"])
        c.fixed_key = row["idempotent_key"]
        return c

    def approve(self, uid: str, reviewer: str, note: str) -> dict[str, Any]:
        """Records a human agreement. All gates are checked again first, and nothing is sent to MT5."""
        row = self._candidate(uid)
        now = self.now()
        lifecycle.check(row["status"], "approved")
        if row["expires_at"] <= now:
            self.repo.move(uid, "expired", "system", "Expired before approval.", now)
            raise ApprovalBlocked("This candidate has expired.")
        ctx = self.builder.build(row["symbol"], now)
        pending = [{"symbol": a["symbol"], "direction": a["direction"], "key": a["idempotent_key"]} for a in self.repo.active(row["symbol"])]
        gates = run_gates(ctx, self._as_candidate(row, now), self.cfg, pending)
        self.repo.replace_gates(uid, gates, now)
        failed = first_failure(gates)
        if failed:
            raise ApprovalBlocked(f"The {failed} gate fails now, so this candidate cannot be approved.")
        return self._view(self.repo.move(uid, "approved", f"human:{reviewer}", note, now))

    def reject(self, uid: str, reviewer: str, note: str) -> dict[str, Any]:
        row = self._candidate(uid)
        return self._view(self.repo.move(uid, "rejected", f"human:{reviewer}", note, self.now(), rejected_by=f"human:{reviewer}"))

    def reopen(self, uid: str, reviewer: str, note: str) -> dict[str, Any]:
        """A person overrides a rejection. Only the AI's rejection can be re-opened: gates clear by themselves when conditions do."""
        row = self._candidate(uid)
        if row["status"] != "rejected" or row["rejected_by"] != "ai_reviewer":
            raise ApprovalBlocked("Only a candidate rejected by the AI reviewer can be re-opened.")
        return self._view(self.repo.move(uid, "under_review", f"human:{reviewer}", note, self.now(), rejected_by=None))

    def expire_stale(self, now: datetime | None = None) -> int:
        """Moves active candidates past their expiry, or past their stop before any decision, to ``expired``."""
        self._require_schema()
        t = now or self.now()
        moved = 0
        closes: dict[str, float | None] = {}
        for row in self.repo.active():
            symbol = row["symbol"]
            if symbol not in closes:
                closes[symbol] = self._last_close(symbol, t)
            reason = "Expiry time passed." if row["expires_at"] <= t else _stop_broken(row, closes[symbol])
            if reason:
                self.repo.move(row["candidate_uid"], "expired", "system", reason, t)
                moved += 1
        return moved

    def _last_close(self, symbol: str, t: datetime) -> float | None:
        df = self.technical.candles.load_candles(symbol, self.cfg.outcomes["candle_timeframe"])
        seen = df[df["ts_utc"] <= t]
        return None if seen.empty else float(seen["close"].iloc[-1])

    # ------------------------------------------------------------------------------------------- outcomes
    def update_outcomes(self, now: datetime | None = None) -> dict[str, int]:
        self._require_schema()
        tf = self.cfg.outcomes["candle_timeframe"]
        frames: dict[str, Any] = {}
        counts: dict[str, int] = {}
        for row in self.repo.unresolved():
            symbol = row["symbol"]
            if symbol not in frames:
                frames[symbol] = self.technical.candles.load_candles(symbol, tf)
            df = frames[symbol]
            after = df[df["ts_utc"] >= row["as_of"]]
            o = label(row["direction"], row["entry_low"], row["entry_high"], row["stop_loss"], row["take_profit"], row["expires_at"], after,
                      self.cfg.outcomes["max_hold_candles"])
            self.repo.save_outcome(row["candidate_uid"], touched=o.touched, filled=o.filled, result=o.result, r_multiple=o.r_multiple,
                                   mfe_r=o.mfe_r, mae_r=o.mae_r, resolved_at=o.resolved_at)
            counts[o.result] = counts.get(o.result, 0) + 1
        return counts

    # ------------------------------------------------------------------------------------------- backtest
    def backtest(self, start: date | datetime, end: date | datetime, symbols: list[str] | None = None, template: str | None = None,
                 progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        self._require_schema()
        begin, finish = _day(start), _day(end)
        if begin >= finish:
            raise ValueError("The backtest start must be before its end.")
        bt = self.cfg.backtest
        split = _day(bt["split_date"]) if bt["split_date"] else None
        chosen = [s.upper() for s in symbols] if symbols else self.symbols()
        fast = ReplayTechnical(self.technical)
        builder = ContextBuilder(self.cfg, fast, IntelligenceAdapter(self.intelligence, include_module03=False), NullAccount(),
                                 NullLearning(), SpreadSource(fast.candles, cache=True), self._specs())
        trades: list[SimTrade] = []
        result = ReplayResult()
        for symbol in chosen:
            frame = fast.candles.load_candles(symbol, self.cfg.outcomes["candle_timeframe"])
            r = replay_symbol(symbol, begin, finish, self.cfg, builder, frame, [template] if template else None, progress)
            trades += r.trades
            result.steps += r.steps
            result.candidates += r.candidates
            result.open_at_end += r.open_at_end
            for k, v in r.blocked.items():
                result.blocked[k] = result.blocked.get(k, 0) + v
        rep = report(trades, self.cfg.sizing["default_risk_pct"], bt["account_start"], split, bt["small_sample"])
        rep["details"].update(steps=result.steps, candidates_seen=result.candidates, blocked_by_gate=result.blocked,
                              still_open_at_end=result.open_at_end, slippage_r=bt["slippage_r"], ai_reviewer="not used in backtests",
                              disclaimer="Simulated on stored history with stated assumptions. Past results do not predict future results.")
        uid = self.repo.save_backtest(self.cfg.fingerprint, chosen, begin, finish, split, rep, trades)
        return {**rep, "run": {"id": uid, "symbols": chosen, "from": begin, "to": finish}}

    def latest_backtest(self) -> dict[str, Any] | None:
        self._require_schema()
        return self.repo.latest_backtest()

    # ------------------------------------------------------------------------------------------- reads
    def signals(self, status: str | None = None, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_schema()
        rows = self.repo.listing(status, symbol.upper() if symbol else None, limit)
        evidence = self.repo.evidence_sentences([r["candidate_uid"] for r in rows])
        return [frontend_views.signal(r, evidence[r["candidate_uid"]]) for r in rows]

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.dossier(row["candidate_uid"])

    def dossier(self, uid: str) -> dict[str, Any]:
        row = self._candidate(uid)
        evidence = self.repo.evidence(uid)
        return {**frontend_views.signal(row, [e["sentence"] for e in evidence]), "take_profit2": row["take_profit2"],
                "expires_at": row["expires_at"], "invalidation": row["invalidation"], "grade": row["grade"],
                "deterministic_score": row["deterministic_score"], "rejected_by": row["rejected_by"],
                "review_status": frontend_views.review_status(row, self.repo.history(uid)),
                "suggested_volume": row["suggested_volume"], "volume_reason": row["volume_reason"], "evidence_detail": evidence,
                "gates": self.repo.gates(uid), "ai_review": frontend_views.ai_view(self.ai.audit.latest(uid)),
                "status_history": self.repo.history(uid), "outcome": self.repo.outcome(uid), "availability": row["availability"],
                "disclaimer": DISCLAIMER}

    def context(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        ctx = self.builder.build(symbol, as_of)
        return {"symbol": ctx.symbol, "as_of": ctx.as_of, "input_hash": ctx.input_hash, "availability": ctx.availability,
                "consensus": ctx.consensus, "event_state": ctx.event_state, "volatility_regime": ctx.regime, "condition_tags": ctx.tags,
                "sessions": {k: ctx.sessions.get(k) for k in ("market_open", "active_sessions", "active_kill_zones")},
                "cot": ctx.cot, "spread": ctx.spread, "account": {k: v for k, v in ctx.account.items() if k != "limits"},
                "disclaimer": DISCLAIMER}

    # ------------------------------------------------------------------------------------------- /ai surface
    def analyses(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self._require_schema()
        return [frontend_views.analysis_row(r) for r in self.repo.list_runs(symbol.upper() if symbol else None, limit)]

    def analysis(self, uid: str) -> dict[str, Any]:
        self._require_schema()
        row = self.repo.get_run(uid)
        if row is None:
            raise CandidateNotFound(f"Unknown analysis '{uid}'.")
        return {**frontend_views.analysis_row(row), "availability": row["availability"], "summary": row["summary"], "disclaimer": DISCLAIMER}

    def bias(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        return {"symbol": symbol.upper(), **assess_bias(self.builder.build(symbol, as_of), self.cfg), "disclaimer": DISCLAIMER}

    def regime(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        return {"symbol": symbol.upper(), **assess_regime(self.builder.build(symbol, as_of), self.cfg), "disclaimer": DISCLAIMER}

    def setups(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        """The strategy scan, without saving anything."""
        ctx = self.builder.build(symbol, as_of)
        rows = []
        for c, s in candidates_at(ctx, self.cfg):
            gates = run_gates(ctx, c, self.cfg)
            rows.append({"strategy": c.template, "direction": c.direction, "instrument": c.symbol, "timeframe": c.timeframe, "evidence_grade": s.grade,
                         "score": s.confidence, "valid": first_failure(gates) is None, "blocked_by": first_failure(gates), "invalidation": c.invalidation})
        return {"symbol": ctx.symbol, "as_of": ctx.as_of, "setups": rows, "skipped": [x.__dict__ for x in generate(ctx, self.cfg)[1]], "disclaimer": DISCLAIMER}

    def trade_ideas(self, status: str | None = None, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_schema()
        rows = self.repo.listing(status, symbol.upper() if symbol else None, limit)
        evidence = self.repo.evidence_sentences([r["candidate_uid"] for r in rows])
        return [frontend_views.trade_idea(r, evidence[r["candidate_uid"]], self.repo.history(r["candidate_uid"])) for r in rows]

    def strategies(self) -> list[dict[str, Any]]:
        return [{"id": name, "version": ENGINE_VERSION, "enabled": cfg["enabled"], "require_alignment": cfg["require_alignment"],
                 "timeframes": cfg.get("timeframes") or self.cfg.entry_timeframes} for name, cfg in self.cfg.templates.items()]

    def config_view(self) -> dict[str, Any]:
        return {"config": self.cfg.data, "fingerprint": self.cfg.fingerprint, "overrides": self._saved_overrides()}

    def _overrides_path(self) -> Path:
        return Path(self.settings.resolve(self.settings.data_dir)) / "decision" / "config_overrides.json"

    def _saved_overrides(self) -> dict[str, Any]:
        path = self._overrides_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def config_update(self, overrides: dict[str, Any], reviewer: str) -> dict[str, Any]:
        """Validated first, then stored: a bad key or value changes nothing. Each run records the fingerprint it used."""
        self.cfg = with_overrides(self.cfg, overrides)
        self.ai.cfg = self.cfg
        self._builder = None
        path = self._overrides_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_deep(self._saved_overrides(), overrides), indent=2), encoding="utf-8")
        log.info("Decision config changed", extra={"reviewer": reviewer, "fingerprint": self.cfg.fingerprint})
        return self.config_view()

    def providers(self) -> dict[str, Any]:
        return {"providers": provider_list(self.settings), "state": self.ai.state() if self.repo.schema_ready() else None, "disclaimer": DISCLAIMER}

    def health(self) -> dict[str, Any]:
        ready = self.repo.schema_ready()
        runs = self.repo.recent_runs(1) if ready else []
        return {"status": "ok" if ready else "not_initialised", "schema_ready": ready, "engine_version": ENGINE_VERSION,
                "last_run": runs[0]["created_at"] if runs else None, "ai_blocked_reason": self.ai.blocked_reason(), "disclaimer": DISCLAIMER}

    def evaluations(self, limit: int = 20) -> list[dict[str, Any]]:
        self._require_schema()
        return self.repo.list_backtests(limit)

    def evaluation(self, uid: str) -> dict[str, Any]:
        self._require_schema()
        rep = self.repo.get_backtest(uid)
        if rep is None:
            raise CandidateNotFound(f"Unknown evaluation '{uid}'.")
        return rep

    def pending_approvals(self) -> int:
        counts = self.repo.counts()
        return sum(counts.get(s, 0) for s in lifecycle.ACTIVE)

    def status(self) -> dict[str, Any]:
        ready = self.repo.schema_ready()
        return {"schema_ready": ready, "engine_version": ENGINE_VERSION, "config_fingerprint": self.cfg.fingerprint,
                "counts": self.repo.counts() if ready else {}, "recent_runs": self.repo.recent_runs() if ready else [],
                "ai_reviewer": self.ai.state(), "budget_month": budget.month_key(),
                "calibration": calibration(self.repo.resolved(), self.cfg.score["min_outcomes_for_calibration"]) if ready else {},
                "disclaimer": DISCLAIMER}


def _deep(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _stop_broken(row: dict[str, Any], price: float | None) -> str | None:
    if price is None:
        return None
    broken = price < row["stop_loss"] if row["direction"] == "long" else price > row["stop_loss"]
    return f"Price closed beyond the stop ({price})." if broken else None


def _day(value: Any) -> datetime:
    if isinstance(value, datetime):
        return utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return _day(date.fromisoformat(str(value)))
