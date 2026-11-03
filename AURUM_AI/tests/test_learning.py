"""Trade Learning Agent: discovery, forward trials, replication, self-termination, persistence, sources.

All trades here are synthetic test fixtures with a deliberately planted edge, so every rule can be
checked exactly. They are not market data and are never shown in the product.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aurum.gateways.mt5_gateway import ReadOnlyMT5, ReadOnlyViolation
from aurum.learning.engine import TradeLearningAgent
from aurum.learning.features import session_of
from aurum.learning.models import LearningConfig, Stats, Trade
from aurum.learning.sources import deals_to_trades, load_csv
from aurum.learning.store import LineageStore

T0 = datetime(2026, 7, 6, tzinfo=timezone.utc)  # a Monday, London on BST


class Feed:
    """Generates closed trades in time order. London (10:00 UTC) = buys, Asia (02:00 UTC) = sells."""

    def __init__(self) -> None:
        self.n = 0

    def trade(self, where: str, pnl: float) -> Trade:
        day = T0 + timedelta(days=self.n // 4)
        hour = 10 if where == "london" else 2
        opened = day.replace(hour=hour)
        self.n += 1
        return Trade(ticket=f"T{self.n}", symbol="EURUSD", side="buy" if where == "london" else "sell", volume=0.1,
                     open_time=opened, close_time=opened + timedelta(hours=2), open_price=1.1, close_price=1.1, pnl=pnl)

    def edge_block(self, n: int) -> list[Trade]:
        """London buys win (+30, one in four loses 10); Asia sells lose (+10 / -14 alternating)."""
        out = []
        for i in range(n):
            if i % 5 < 2:
                out.append(self.trade("london", -10.0 if (self.n // 5) % 4 == 0 else 30.0))
            else:
                out.append(self.trade("asia", 10.0 if i % 2 else -14.0))
        return out

    def london(self, n: int, pnl: float) -> list[Trade]:
        return [self.trade("london", pnl) for _ in range(n)]

    def asia(self, n: int, pnl: float = -5.0) -> list[Trade]:
        return [self.trade("asia", pnl) for _ in range(n)]


def kinds(agent: TradeLearningAgent) -> list[str]:
    return [e.kind for a in agent.lineage.agents for e in a.events]


def test_stats_are_computed_from_the_trades():
    f = Feed()
    s = Stats.of([f.trade("london", 30), f.trade("london", -10), f.trade("london", 20)])
    assert (s.trades, s.wins, s.net, s.gross_profit, s.gross_loss) == (3, 2, 40, 50, 10)
    assert s.profit_factor == 5 and round(s.win_rate, 3) == 0.667 and s.expectancy == pytest.approx(40 / 3)
    assert Stats.of([]).profit_factor is None


def test_sessions_follow_each_markets_clock():
    assert session_of(datetime(2026, 7, 1, 7, 30, tzinfo=timezone.utc)) == "london"  # 08:30 BST
    assert session_of(datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)) == "asia"  # 07:30 GMT, Tokyo 16:30
    assert session_of(datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)) == "london-newyork overlap"
    assert session_of(datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)) == "off-hours"


def test_discovers_the_planted_edge_and_starts_a_trial():
    f, agent = Feed(), TradeLearningAgent.start(at=T0)
    agent.observe(f.edge_block(99))
    assert agent.lineage.active_agent.status == "discovering"  # window not full yet
    agent.observe(f.edge_block(1))
    g1 = agent.lineage.active_agent
    assert g1.status == "trialling"
    # London trades are all buys, so "session = london" and "side = buy" pick exactly the same trades.
    assert g1.pattern in ((("session", "london"),), (("side", "buy"),))
    assert g1.discovery_stats["trades"] >= 20 and g1.discovery_stats["profit_factor"] >= 1.3


def test_a_passed_trial_replicates_into_one_child_that_repeats_the_cycle():
    f, agent = Feed(), TradeLearningAgent.start(at=T0)
    agent.observe(f.edge_block(100))
    agent.observe(f.edge_block(30))  # forward trades keep the same edge
    g1, g2 = agent.lineage.agents[0], agent.lineage.agents[1]
    assert g1.status == "replicated" and g1.trials[0]["passed"] is True
    assert len(g1.trials[0]["tickets"]) == 10  # judged on 10 matching forward trades only
    assert g2.parent == g1.id and g2.generation == 2
    assert agent.lineage.active_agent is g2 and g2.status == "trialling"  # child re-ran discovery at once
    assert sum(1 for a in agent.lineage.agents if a.active) == 1  # never a fan-out


def test_three_failed_trials_in_a_row_terminate_the_agent():
    cfg = LearningConfig(discovery_window=400, features=("symbol", "side", "session"))
    f, agent = Feed(), TradeLearningAgent.start(cfg, at=T0)
    agent.observe(f.edge_block(400))
    for _ in range(10):  # the edge stops working; feed losses until the agent gives up
        if agent.lineage.active_agent is None:
            break
        agent.observe(f.london(10, -50.0))
    g1 = agent.lineage.agents[0]
    assert [t["passed"] for t in g1.trials] == [False, False, False]
    assert len({tuple(map(tuple, t["pattern"])) for t in g1.trials}) == 3  # a failed pattern is never re-tried
    assert g1.status == "terminated" and "terminated" in kinds(agent)
    assert agent.lineage.status == "terminated" and agent.lineage.active_agent is None
    before = len(g1.events)
    agent.observe(f.edge_block(50))  # a terminated lineage does nothing more
    assert len(g1.events) == before and len(agent.lineage.agents) == 1


def test_a_success_resets_the_failure_count():
    cfg = LearningConfig(discovery_window=400, features=("symbol", "side", "session"))
    f, agent = Feed(), TradeLearningAgent.start(cfg, at=T0)
    agent.observe(f.edge_block(400))
    agent.observe(f.london(10, -50.0))
    agent.observe(f.london(10, 25.0))
    g1 = agent.lineage.agents[0]
    assert [t["passed"] for t in g1.trials] == [False, True] and g1.failed_trials == 0
    assert agent.lineage.agents[1].failed_trials == 0


def test_too_few_matching_trades_is_an_inconclusive_failed_trial():
    cfg = LearningConfig(trial_max_wait=15)
    f, agent = Feed(), TradeLearningAgent.start(cfg, at=T0)
    agent.observe(f.edge_block(100))
    agent.observe(f.asia(15))
    trial = agent.lineage.agents[0].trials[0]
    assert trial["passed"] is False and trial["inconclusive"] is True


def test_generation_cap_stops_replication():
    cfg = LearningConfig(max_generations=1)
    f, agent = Feed(), TradeLearningAgent.start(cfg, at=T0)
    agent.observe(f.edge_block(130))
    assert [a.status for a in agent.lineage.agents] == ["capped"]
    assert agent.lineage.status == "capped"


def test_duplicate_tickets_are_ignored():
    f, agent = Feed(), TradeLearningAgent.start(at=T0)
    trades = f.edge_block(60)
    agent.observe(trades)
    agent.observe(trades)
    assert agent.lineage.trades_observed == 60


def test_lineage_survives_a_restart_mid_trial(tmp_path):
    f = Feed()
    trades = f.edge_block(100) + f.edge_block(30)
    straight = TradeLearningAgent.start(at=T0)
    straight.observe(trades)

    store = LineageStore(tmp_path)
    first = TradeLearningAgent.start(at=T0)
    first.observe(trades[:105])
    store.save(first.lineage)
    resumed = TradeLearningAgent(store.load("L1"))
    resumed.observe(trades[105:])
    assert [(a.id, a.status, a.trials) for a in resumed.lineage.agents] == [(a.id, a.status, a.trials) for a in straight.lineage.agents]


def test_deals_are_rebuilt_into_closed_trades_with_net_pnl():
    utc = lambda s: datetime.fromtimestamp(s, tz=timezone.utc)  # noqa: E731
    deals = [
        {"ticket": 1, "type": 2, "entry": 0, "position_id": 0, "profit": 1000.0},  # balance deposit: ignored
        {"ticket": 2, "type": 0, "entry": 0, "position_id": 7, "symbol": "EURUSD", "volume": 0.2, "price": 1.1,
         "time": 1_700_000_000, "commission": -1.4, "profit": 0.0, "comment": "london-breakout"},
        {"ticket": 3, "type": 1, "entry": 1, "position_id": 7, "symbol": "EURUSD", "volume": 0.2, "price": 1.102,
         "time": 1_700_003_600, "commission": -1.4, "swap": -0.2, "profit": 40.0},
        {"ticket": 4, "type": 1, "entry": 0, "position_id": 8, "symbol": "XAUUSD", "volume": 0.1, "price": 2600.0,
         "time": 1_700_004_000},  # still open: ignored
    ]
    (t,) = deals_to_trades(deals, utc)
    assert (t.ticket, t.side, t.volume, t.pnl, t.strategy) == ("7", "buy", 0.2, 37.0, "london-breakout")
    assert t.close_time - t.open_time == timedelta(hours=1)


def test_csv_source_validates_columns(tmp_path):
    good = tmp_path / "trades.csv"
    good.write_text("ticket,symbol,side,open_time,close_time,pnl,strategy\n"
                    "1,eurusd,BUY,2026-07-06 10:00,2026-07-06T12:00:00Z,25.5,breakout\n", encoding="utf-8")
    (t,) = load_csv(good)
    assert (t.symbol, t.side, t.pnl, t.strategy, t.close_time.tzinfo is not None) == ("EURUSD", "buy", 25.5, "breakout", True)
    bad = tmp_path / "bad.csv"
    bad.write_text("ticket,symbol\n1,EURUSD\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        load_csv(bad)


def test_trade_history_is_readable_but_trading_stays_blocked():
    class Mod:
        def history_deals_get(self, *a):
            return []

        def order_send(self, *a):  # pragma: no cover - must never be reachable
            raise AssertionError("order_send reached")

    proxy = ReadOnlyMT5(Mod())
    assert proxy.history_deals_get(None, None) == []
    with pytest.raises(ReadOnlyViolation):
        proxy.order_send({})


# ------------------------------------------------------------------ CLI and API
def _write_csv(path, trades):
    rows = ["ticket,symbol,side,open_time,close_time,pnl"]
    rows += [f"{t.ticket},{t.symbol},{t.side},{t.open_time.isoformat()},{t.close_time.isoformat()},{t.pnl}" for t in trades]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_cli_runs_from_csv_continues_and_resets(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import aurum.cli as main_cli
    import aurum.learning.cli as learn_cli

    store = LineageStore(tmp_path / "learning")
    monkeypatch.setattr(learn_cli, "_store", lambda: store)
    runner, f = CliRunner(), Feed()
    first = tmp_path / "week1.csv"
    _write_csv(first, f.edge_block(100))
    r = runner.invoke(main_cli.app, ["learn", "run", "--csv", str(first)])
    assert r.exit_code == 0, r.output
    assert "What works" in r.output and "Now trialling" in r.output

    second = tmp_path / "week2.csv"
    _write_csv(second, f.edge_block(30))
    r = runner.invoke(main_cli.app, ["learn", "run", "--csv", str(second)])
    assert r.exit_code == 0 and "Replicated" in r.output and "L1-G2" in r.output

    assert "L1-G2" in runner.invoke(main_cli.app, ["learn", "status"]).output
    assert runner.invoke(main_cli.app, ["learn", "reset"]).exit_code == 1  # needs --yes
    assert runner.invoke(main_cli.app, ["learn", "reset", "--yes"]).exit_code == 0
    assert store.load("L1") is None


def test_api_serves_the_lineage_without_raw_trade_buffers(tmp_path):
    from fastapi.testclient import TestClient
    from pathlib import Path

    from aurum.api.app import create_app
    from aurum.config import Settings
    from aurum.database.connection import engine_from_settings
    from cot_helpers import cot_cfg

    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
                        data_dir=tmp_path / "data", log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC")
    with TestClient(create_app(settings, cot_cfg(), engine_from_settings(settings))) as c:
        assert c.get("/api/v1/agents/learning").json() == {"lineage": None}
        f, agent = Feed(), TradeLearningAgent.start(at=T0)
        agent.observe(f.edge_block(130))
        LineageStore(Path(settings.resolve(settings.data_dir)) / "learning").save(agent.lineage)
        body = c.get("/api/v1/agents/learning").json()["lineage"]
    assert body["status"] == "running" and body["activeAgent"] == "L1-G2" and body["readOnly"] is True
    assert [a["status"] for a in body["agents"]] == ["replicated", "trialling"]
    assert "window" not in body and "trialMatched" not in body["agents"][0]
    assert body["agents"][0]["trials"][0]["passed"] is True
