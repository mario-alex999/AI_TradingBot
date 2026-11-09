"""The `risk` CLI renders its tables (simulated data only; no broker, no live database)."""
from __future__ import annotations

from typer.testing import CliRunner

from aurum.cli import app
from aurum.ui import Table


def test_table_accepts_positional_headers_with_numeric_columns():
    t = Table("Scenario", "PASS", numeric={"PASS"})
    assert [c.justify for c in t.columns] == ["left", "right"]


def test_risk_simulate_runs_and_writes_report(tmp_path):
    report = tmp_path / "sim.md"
    result = CliRunner().invoke(app, ["risk", "simulate", "--report", str(report)], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    assert "normal_day" in result.output and "SIMULATION" in result.output
    text = report.read_text(encoding="utf-8")
    assert "losing_streak" in text and "daily_loss_breach" in text
