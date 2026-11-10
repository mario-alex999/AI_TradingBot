"""python -m aurum.cli mt5-slots setup|status - terminal slots for per-person MT5 links."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from aurum.mt5link import terminals
from aurum.ui import Table, console, error

mt5_slots_app = typer.Typer(add_completion=False, no_args_is_help=True,
                            help="Terminal slots: one portable MT5 terminal copy per person linked at the same time.")


@mt5_slots_app.command("setup")
def setup(count: int = typer.Option(..., min=1, max=32, help="How many people may link an account at the same time."),
          source: Path = typer.Option(terminals.DEFAULT_SOURCE, help="The installed MetaTrader 5 folder to copy."),
          dest: Optional[Path] = typer.Option(None, help="Where the slots go (default: MT5_SLOTS_DIR).")) -> None:
    """Copy the installed terminal into slot folders (existing slots are kept). While linked, each slot uses
    roughly 200-400 MB of memory."""
    from aurum.config import get_settings
    dest = dest or get_settings().mt5_slots_dir
    try:
        rows = terminals.setup_slots(source, dest, count)
    except (FileNotFoundError, OSError) as exc:
        error(str(exc))
        raise typer.Exit(1)
    t = Table(title=f"MT5 terminal slots in {dest}")
    for c in ("Slot", "Folder", "Created now", "Server list"):
        t.add_column(c)
    for r in rows:
        t.add_row(str(r["slot"]), r["folder"], "yes" if r["created"] else "already there", "yes" if r["serversDat"] else "missing")
    console.print(t)
    console.print(f"Set [bold]MT5_SLOT_COUNT={count}[/] (and MT5_SLOTS_DIR if you changed --dest) in .env, then restart the API.")
    if not all(r["serversDat"] for r in rows):
        console.print("No broker server list was found; log in to your broker once in the main terminal, then run setup again.")


@mt5_slots_app.command("status")
def status() -> None:
    """Show the configured slots and which are linked right now."""
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    from aurum.mt5link.service import Mt5LinkService
    s = get_settings()
    svc = Mt5LinkService(engine_from_settings(s), s)
    console.print(svc.status())
