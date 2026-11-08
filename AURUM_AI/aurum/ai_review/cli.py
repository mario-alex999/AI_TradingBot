"""CLI for the optional AI review: python -m aurum.cli ai-review status|run|history."""
from __future__ import annotations

from typing import Any, Optional

import typer

from aurum.ui import Table, console, error

from .service import AiReviewError, AiReviewService, ReviewFailed

ai_review_app = typer.Typer(help="Optional advisory AI review of stored analysis (off by default). Never places trades.")


def _service() -> AiReviewService:
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    settings = get_settings()
    return AiReviewService(settings, engine_from_settings(settings))


def _fail(msg: str) -> None:
    error(msg)
    raise typer.Exit(1)


def _print_review(r: dict[str, Any]) -> None:
    v = r["verdict"]
    console.rule(f"[bold]AI review · {r['symbol']}[/] [muted](advisory)[/]", style="muted", align="left")
    console.print(f"[bold]{v['stance']}[/] · confidence {v['confidence']}\n\n{v['summary']}\n")
    for title, key in (("Supporting", "supporting_evidence"), ("Conflicting", "conflicting_evidence"), ("Risks", "risks"),
                       ("Data gaps", "data_gaps")):
        if v[key]:
            console.print(f"[muted]{title}[/]")
            for item in v[key]:
                console.print(f"  - {item}")
    console.print(f"[muted]Would change this view[/]\n  {v['what_would_change_this_view']}")
    console.print(f"\n[muted]{r['model']} · {r['input_tokens']:,} in / {r['output_tokens']:,} out · ${r['cost_usd']:.4f}[/]")
    console.print(f"[muted]{r['disclaimer']}[/]")


@ai_review_app.command("status")
def status() -> None:
    """Show whether the review is on, and this month's budget."""
    st = _service().status()
    t = Table(title="AI review")
    t.add_column("Setting")
    t.add_column("Value")
    for name, value in (("Enabled", st["enabled"]), ("Ready", st["ready"]), ("API key set", st["api_key_configured"]),
                        ("Model", st["model"]), ("Effort", st["effort"]), ("Month", st["month"]),
                        ("Budget USD", st["budget_usd"]), ("Spent or reserved USD", st["committed_usd"]),
                        ("Remaining USD", st["remaining_usd"]), ("Completed reviews", st["completed_reviews_this_month"])):
        t.add_row(name, "—" if value is None else str(value))
    console.print(t)
    if st["reason"]:
        console.print(f"[muted]{st['reason']}[/]")


@ai_review_app.command("run")
def run(symbol: str = typer.Argument(..., help="Instrument, e.g. EURUSD"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be sent and reserved. No API call, no cost.")) -> None:
    """Review one instrument. This calls the Anthropic API and spends budget unless --dry-run is given."""
    svc = _service()
    try:
        if dry_run:
            est = svc.estimate(symbol)
            console.print(f"{est['symbol']}: {est['context_chars']:,} characters of context for {est['model']}.")
            if est["worst_case_usd"] is None:
                console.print("This model has no known price, so a review would be refused.")
            else:
                console.print(f"A review would reserve up to ${est['worst_case_usd']:.4f}. No API call was made.")
            return
        _print_review(svc.run(symbol))
    except ReviewFailed as exc:
        _fail(f"{exc} (recorded cost ${exc.review['cost_usd']:.4f})")
    except AiReviewError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - e.g. unknown instrument from the analysis services
        if type(exc).__name__ == "UnknownInstrumentError":
            _fail(str(exc.args[0]))
        raise


@ai_review_app.command("history")
def history(symbol: Optional[str] = typer.Option(None, "--symbol"),
            limit: int = typer.Option(20, "--limit", min=1, max=200)) -> None:
    """List stored reviews, newest first."""
    t = Table(title="AI reviews", numeric=("Cost USD",))
    for c in ("When (UTC)", "Symbol", "Status", "Stance", "Confidence", "Cost USD"):
        t.add_column(c)
    for r in _service().history(symbol, limit):
        v = r["verdict"] or {}
        t.add_row(f"{r['created_at']:%Y-%m-%d %H:%M}", r["symbol"], r["status"], v.get("stance") or r["error_code"] or "—",
                  v.get("confidence") or "—", f"{r['cost_usd']:.4f}")
    console.print(t)
