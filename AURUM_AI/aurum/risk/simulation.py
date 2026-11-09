"""Risk-engine simulation with no broker and no live data.

``MemoryRiskData`` implements the same RiskData interface as the database provider, from in-memory account snapshots,
positions, closed trades, specifications, quotes and price series. ``run_scenario`` drives the REAL engine (rules, locks,
reservations, persistence in a throw-away SQLite database) through a sequence of steps and reports what it decided.

Results are SIMULATED risk decisions on assumed inputs. They are not account performance, not a backtest of a strategy, and
not evidence of future profitability or guaranteed capital protection.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from aurum.risk.account import BalanceOperation, ClosedTrade, EquityPoint, Snapshot
from aurum.risk.calculator import CalcError, InstrumentSpec, Quote, Rate
from aurum.risk.money import D
from aurum.risk.portfolio import Position

SIM_NOTICE = ("SIMULATION: risk decisions on assumed inputs in a throw-away database. Not real account performance and not a "
              "prediction of future results or a guarantee of capital protection.")

# Illustrative specifications for tests and simulation ONLY. They resemble a typical MT5 demo server (they match the
# specifications stored on this project's demo server on 2026-11-08) but are not universal facts: live assessments always
# use the broker's stored specification.
SAMPLE_SPECS: dict[str, dict[str, Any]] = {
    "EURUSD": {"asset_class": "forex", "digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
               "contract_size": 100000, "volume_min": 0.01, "volume_max": 200, "volume_step": 0.01, "currency_base": "EUR",
               "currency_profit": "USD", "raw": {"trade_calc_mode": 0, "trade_stops_level": 0}},
    "GBPUSD": {"asset_class": "forex", "digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
               "contract_size": 100000, "volume_min": 0.01, "volume_max": 200, "volume_step": 0.01, "currency_base": "GBP",
               "currency_profit": "USD", "raw": {"trade_calc_mode": 0, "trade_stops_level": 0}},
    "XAUUSD": {"asset_class": "metal", "digits": 3, "point": 0.001, "tick_size": 0.001, "tick_value": 0.1,
               "contract_size": 100, "volume_min": 0.01, "volume_max": 200, "volume_step": 0.01, "currency_base": "XAU",
               "currency_profit": "USD", "raw": {"trade_calc_mode": 0, "trade_stops_level": 0}},
    "US30": {"asset_class": "index", "digits": 1, "point": 0.1, "tick_size": 0.1, "tick_value": 0.1, "contract_size": 1,
             "volume_min": 0.05, "volume_max": 500, "volume_step": 0.01, "currency_base": "USD", "currency_profit": "USD",
             "raw": {"trade_calc_mode": 4, "trade_stops_level": 0}},
}
SAMPLE_PRICES = {"EURUSD": (1.10000, 0.00012), "GBPUSD": (1.30000, 0.00020), "XAUUSD": (2650.000, 0.250), "US30": (44000.0, 1.5)}


def calm_intelligence(symbol: str, regime: str = "NORMAL", event_state: str = "NORMAL", market_open: bool = True,
                      sessions: list[str] | None = None, atr: float | None = None) -> dict[str, Any]:
    """A Module 04-shaped result for simulation and tests."""
    price, _ = SAMPLE_PRICES.get(symbol, (1.0, 0.0))
    return {"sessions": {"market_open": market_open, "active_sessions": sessions if sessions is not None else ["london"]},
            "event_risk": {"state": event_state, "reason": "simulated", "coverage": {"complete": True}},
            "volatility": {"regime": regime, "timeframes": {"H1": {"atr": atr if atr is not None else price * 0.0015}}},
            "integrations": {"module_02": {"available": False, "reason": "simulated"}}}


@dataclass
class MemoryRiskData:
    now: datetime
    currency: str = "USD"
    balance: Decimal = Decimal("10000")
    equity: Decimal = Decimal("10000")
    margin: Decimal = Decimal("0")
    leverage: int = 100
    trade_mode: str = "DEMO"
    snapshot_at: datetime | None = None
    has_snapshot: bool = True
    history: list[EquityPoint] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    closed: list[ClosedTrade] = field(default_factory=list)
    operations: list[BalanceOperation] = field(default_factory=list)
    opened_today: int = 0
    specs: dict[str, dict[str, Any]] = field(default_factory=lambda: json.loads(json.dumps(SAMPLE_SPECS)))
    spec_time: datetime | None = None
    quotes: dict[str, Quote] = field(default_factory=dict)
    medians: dict[str, Decimal] = field(default_factory=dict)
    series: dict[str, pd.Series] = field(default_factory=dict)
    intel: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    intel_reason: str | None = None
    issues: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ideas: dict[str, dict[str, Any]] = field(default_factory=dict)
    rates: dict[tuple[str, str], Rate] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for sym, (price, spread) in SAMPLE_PRICES.items():
            if sym not in self.quotes:
                self.set_quote(sym, price, spread)
            self.intel.setdefault(sym, calm_intelligence(sym))
        for k, sym in enumerate(SAMPLE_PRICES):
            if sym not in self.series:
                self.series[sym] = self._walk(sym, seed=100 + k)

    # ---- helpers for scenarios and tests -------------------------------------------------------------------------------
    def set_quote(self, symbol: str, bid: float, spread: float, at: datetime | None = None) -> None:
        point = D(self.specs[symbol]["point"])
        q = Quote(D(bid), D(bid) + D(spread), at or self.now, "simulation")
        self.quotes[symbol] = q
        self.medians[symbol] = q.spread / point

    def _walk(self, symbol: str, seed: int, n: int = 400) -> pd.Series:
        """Independent synthetic H1 closes (deterministic), so unrelated instruments have a measurable, low correlation."""
        import random
        rnd = random.Random(seed)
        idx = pd.date_range(end=self.now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1), periods=n, freq="h", tz="UTC")
        price, out = SAMPLE_PRICES[symbol][0], []
        for _ in range(n):
            price *= math.exp(0.001 * rnd.gauss(0, 1))
            out.append(price)
        return pd.Series(out, index=idx)

    def correlated_series(self, a: str, b: str, rho: float, n: int = 400, seed: int = 7) -> None:
        """Synthetic H1 closes with a chosen return correlation (deterministic)."""
        import random
        rnd = random.Random(seed)
        idx = pd.date_range(end=self.now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1), periods=n, freq="h", tz="UTC")
        pa, pb, ra, rb = SAMPLE_PRICES[a][0], SAMPLE_PRICES[b][0], [], []
        for _ in range(n):
            z1, z2 = rnd.gauss(0, 1), rnd.gauss(0, 1)
            x = 0.001 * z1
            y = 0.001 * (rho * z1 + math.sqrt(max(0.0, 1 - rho * rho)) * z2)
            pa *= math.exp(x)
            pb *= math.exp(y)
            ra.append(pa)
            rb.append(pb)
        self.series[a], self.series[b] = pd.Series(ra, index=idx), pd.Series(rb, index=idx)

    # ---- RiskData interface --------------------------------------------------------------------------------------------
    def account_snapshot(self) -> Snapshot | None:
        if not self.has_snapshot:
            return None
        return Snapshot(self.snapshot_at or self.now, 12345, self.currency, self.trade_mode, self.leverage, self.balance,
                        self.equity, self.margin, self.equity - self.margin, None, self.equity - self.balance)

    def equity_history(self, since: datetime, until: datetime) -> list[EquityPoint]:
        return [p for p in self.history if p.at <= until]

    def open_positions(self) -> list[Position]:
        return list(self.positions)

    def closed_trades(self, since: datetime) -> list[ClosedTrade]:
        return [t for t in self.closed if t.close_time >= since]

    def opened_since(self, since: datetime) -> int:
        return self.opened_today

    def balance_operations(self, since: datetime) -> list[BalanceOperation]:
        return [o for o in self.operations if o.at >= since]

    def symbols(self) -> dict[str, str]:
        return {s: v["asset_class"] for s, v in self.specs.items()}

    def spec(self, symbol: str) -> InstrumentSpec:
        if symbol not in self.specs:
            raise CalcError("SPEC_MISSING", f"No stored instrument specification for {symbol}.")
        row = dict(self.specs[symbol])
        row["captured_at"] = self.spec_time or self.now
        return InstrumentSpec.from_row(symbol, row["asset_class"], row)

    def quote(self, symbol: str, now: datetime) -> Quote | None:
        return self.quotes.get(symbol)

    def spread_stats(self, symbol: str, now: datetime) -> tuple[Decimal | None, Decimal | None]:
        q = self.quotes.get(symbol)
        if q is None:
            return None, None
        return q.spread / D(self.specs[symbol]["point"]), self.medians.get(symbol)

    def rate(self, from_ccy: str, to_ccy: str, now: datetime) -> Rate | None:
        if from_ccy == to_ccy:
            return Rate(from_ccy, to_ccy, D(1), now, "identity")
        if (from_ccy, to_ccy) in self.rates:
            return self.rates[(from_ccy, to_ccy)]
        for sym, spec in self.specs.items():
            base, profit = spec.get("currency_base"), spec.get("currency_profit")
            q = self.quotes.get(sym)
            if q is None or (base, profit) not in ((from_ccy, to_ccy), (to_ccy, from_ccy)):
                continue
            mid = (q.bid + q.ask) / 2
            return Rate(from_ccy, to_ccy, mid if base == from_ccy else D(1) / mid, q.at, f"{sym} mid (simulation)")
        return None

    def closes(self, symbol: str, timeframe: str, bars: int, now: datetime) -> pd.Series | None:
        s = self.series.get(symbol)
        return None if s is None else s.tail(bars + 1)

    def intelligence(self, symbol: str, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
        v = self.intel.get(symbol)
        return (v, None) if v is not None else (None, self.intel_reason or "Module 04 unavailable (simulated).")

    def quality_issues(self, symbol: str, now: datetime) -> list[dict[str, Any]]:
        return self.issues.get(symbol, [])

    def trade_idea(self, idea_id: str) -> dict[str, Any] | None:
        return self.ideas.get(idea_id)


# ---- scenario runner -----------------------------------------------------------------------------------------------------
BUILTIN = Path(__file__).with_name("scenarios")


def run_scenario(spec: dict[str, Any], config_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one scenario on a fresh in-memory database. Steps:

    {"advance_minutes": n} | {"propose": {...proposal...}, "fill": true, "outcome_r": -1.0}
    {"equity_change": amount} | {"deposit": amount} | {"open_position": {...}} | {"close_all": true}
    A filled proposal becomes an open position at its final volume; ``outcome_r`` closes it at that multiple of its risk.
    """
    from aurum.database.connection import init_database, make_engine
    from aurum.risk.config import RiskConfig, deep_merge, profile_config
    from aurum.risk.repository import RiskRepository
    from aurum.risk.service import RiskService

    start = datetime.fromisoformat(spec.get("start", "2026-11-04T12:00:00+00:00"))
    clock = {"now": start}
    data = MemoryRiskData(now=start, balance=D(spec.get("balance", 10000)), equity=D(spec.get("balance", 10000)))
    from aurum.risk.calendar import TradingCalendar
    from aurum.risk.config import TradingDay
    cal = TradingCalendar(TradingDay())
    # Starting equity is known at every period boundary up to the scenario start, as a running account mirror would record.
    d = cal.trading_date(cal.month(start).start) - timedelta(days=1)
    while d <= cal.trading_date(start):
        data.history.append(EquityPoint(cal.day_start(d), data.equity, data.balance))
        d += timedelta(days=1)
    for a, b, rho in spec.get("correlations", []):
        data.correlated_series(a, b, rho)
    engine = make_engine("sqlite:///:memory:")
    init_database(engine)
    svc = RiskService(engine, None, data=data, clock=lambda: clock["now"])
    base = profile_config(spec.get("profile", "moderate"))
    overrides = deep_merge(spec.get("config", {}), config_overrides or {})
    if overrides:
        cfg = RiskConfig.model_validate(deep_merge(base.model_dump(mode="json"), overrides))
    else:
        cfg = base
    repo: RiskRepository = svc.repo
    repo.active_config()
    with repo.serialized() as s:
        repo.add_config_version(s, cfg, 1, "simulation", "scenario configuration", [])

    decisions: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    log: list[dict[str, Any]] = []
    theoretical = Decimal(0)
    peak, max_dd, max_exposure = data.equity, Decimal(0), Decimal(0)
    open_meta: dict[str, Decimal] = {}
    n = 0
    for i, step in enumerate(spec.get("steps", [])):
        repeat = int(step.get("repeat", 1))
        for _ in range(repeat):
            n += 1
            if "advance_minutes" in step:
                clock["now"] += timedelta(minutes=step["advance_minutes"])
            data.now = clock["now"]
            for sym in list(data.quotes):
                q = data.quotes[sym]
                data.quotes[sym] = Quote(q.bid, q.ask, clock["now"], q.source)
            if "deposit" in step:
                data.operations.append(BalanceOperation(clock["now"], D(step["deposit"])))
                data.balance += D(step["deposit"])
                data.equity += D(step["deposit"])
            if "equity_change" in step:
                data.equity += D(step["equity_change"])
            if step.get("close_all"):
                data.positions.clear()
            if "open_position" in step:
                p = step["open_position"]
                data.positions.append(Position(f"manual-{i}", p["symbol"], p["side"], D(p["volume"]), D(p["open_price"]),
                                               D(p["stop_loss"]) if p.get("stop_loss") else None, None, clock["now"], None,
                                               data.symbols()[p["symbol"]]))
            if "propose" in step:
                res = svc.engine.assess(dict(step["propose"]), actor="simulation", idempotency_key=f"sim-{n}")
                decisions[res["decision"]] += 1
                for v in res["rule_violations"] + res["warnings"] + res["errors"]:
                    rules[f"{v['rule_id']} {v['name']}"] += 1
                entry = {"step": n, "time": clock["now"].isoformat(), "symbol": res["instrument"], "decision": res["decision"],
                         "volume": res["final_volume"], "risk": res["monetary_risk"],
                         "violations": [v["rule_id"] for v in res["rule_violations"]], "warnings": [v["rule_id"] for v in res["warnings"]],
                         "errors": [v["rule_id"] for v in res["errors"]]}
                if res["decision"] in ("PASS", "REVIEW") and res["monetary_risk"]:
                    theoretical += D(res["monetary_risk"])
                    if step.get("fill", True):
                        svc.set_assessment_state(res["assessment_id"], "consumed", "simulation", "filled", None)
                        pid = res["assessment_id"]
                        prop = step["propose"]
                        data.positions.append(Position(pid, res["instrument"], res["direction"], D(res["final_volume"]),
                                                       D(prop["entry"]), D(prop["stop_loss"]), D(prop.get("take_profit") or 0) or None,
                                                       clock["now"], prop.get("strategy"), res["asset_class"]))
                        data.opened_today += 1
                        open_meta[pid] = D(res["monetary_risk"])
                        if "outcome_r" in step:
                            pnl = open_meta[pid] * D(step["outcome_r"])
                            data.positions = [p for p in data.positions if p.id != pid]
                            data.closed.append(ClosedTrade(pid, res["instrument"], clock["now"], clock["now"] + timedelta(minutes=1), pnl))
                            data.balance += pnl
                            data.equity += pnl
                            entry["pnl"] = float(round(pnl, 2))
                log.append(entry)
            data.history.append(EquityPoint(clock["now"], data.equity, data.balance))
            peak = max(peak, data.equity)
            max_dd = max(max_dd, (peak - data.equity) / peak * 100 if peak > 0 else Decimal(0))
            open_risk = sum(open_meta.get(p.id, Decimal(0)) for p in data.positions)
            max_exposure = max(max_exposure, open_risk / data.equity * 100 if data.equity > 0 else Decimal(0))
    locks = repo.locks(include_history=True)
    breaches = Counter(lk["rule_id"] for lk in locks if lk["rule_id"])
    return {"name": spec.get("name", "scenario"), "notice": SIM_NOTICE, "profile": cfg.profile, "trades_evaluated": sum(decisions.values()),
            "passed": decisions["PASS"], "review": decisions["REVIEW"], "blocked": decisions["BLOCK"], "errors": decisions["ERROR"],
            "total_theoretical_risk": float(round(theoretical, 2)), "max_drawdown_pct": float(round(max_dd, 3)),
            "daily_loss_breaches": breaches["R030"], "weekly_loss_breaches": breaches["R031"],
            "monthly_loss_breaches": breaches["R032"], "drawdown_breaches": breaches["R033"],
            "consecutive_loss_locks": breaches["R035"], "max_aggregate_exposure_pct": float(round(max_exposure, 3)),
            "final_equity": float(round(data.equity, 2)), "top_rules": rules.most_common(10), "locks": len(locks), "log": log}


def load_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    import yaml
    p = path or BUILTIN
    files = [p] if p.is_file() else sorted(p.glob("*.yaml"))
    out = []
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        out.extend(doc if isinstance(doc, list) else [doc])
    return out


def report_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Module 06 risk simulation report", "", f"> {SIM_NOTICE}", "",
             "| Scenario | Evaluated | PASS | REVIEW | BLOCK | ERROR | Theoretical risk | Max DD % | Max exposure % | Daily/weekly breaches | Streak locks |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['trades_evaluated']} | {r['passed']} | {r['review']} | {r['blocked']} | {r['errors']} | "
                     f"{r['total_theoretical_risk']:.2f} | {r['max_drawdown_pct']:.2f} | {r['max_aggregate_exposure_pct']:.2f} | "
                     f"{r['daily_loss_breaches']}/{r['weekly_loss_breaches']} | {r['consecutive_loss_locks']} |")
    lines += ["", "## Most frequently triggered rules", ""]
    for r in results:
        top = ", ".join(f"{k} ({v})" for k, v in r["top_rules"][:5]) or "none"
        lines.append(f"- **{r['name']}**: {top}")
    return "\n".join(lines) + "\n"
