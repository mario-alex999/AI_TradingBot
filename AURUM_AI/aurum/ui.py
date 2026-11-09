"""Terminal presentation for the AURUM CLIs: one theme, one table style.

Mirrors the frontend's "data terminal" rules so the two read as one product:

- colour carries meaning only (positive / negative / warning), plus the brand
  gold for the one heading accent; everything else is ink or a muted grey;
- status words are sentence case ("Success", "Not found"), never shouted;
- tables have no box, one rule under a grey header, numbers right-aligned;
- errors are one line: "Error: what failed and how to fix it".
"""
from __future__ import annotations

from typing import Iterable

from rich import box
from rich.console import Console
from rich.table import Table as _RichTable
from rich.theme import Theme

THEME = Theme(
    {
        "positive": "#16C784",
        "negative": "#F2606A",
        "warning": "#D9A441",
        "accent": "#D6AD60",
        "muted": "#8A8C92",
        "label": "#8A8C92",
    }
)

console = Console(theme=THEME, highlight=False)

# Status words -> semantic style. Anything unknown stays in plain ink.
_STATUS_STYLE = {
    "success": "positive",
    "ok": "positive",
    "yes": "positive",
    "verified": "positive",
    "passed": "positive",
    "partial": "warning",
    "warning": "warning",
    "unknown": "warning",
    "unverified": "warning",
    "skipped": "muted",
    "failed": "negative",
    "error": "negative",
    "no": "negative",
    "mismatch": "negative",
    "not found": "negative",
    "invalid": "negative",
}


def status(word: str) -> str:
    """Rich markup for a status word in sentence case with its semantic colour."""
    text = str(word).replace("_", " ").strip()
    style = _STATUS_STYLE.get(text.lower())
    if text.lower() == "ok":
        shown = "OK"  # an acronym, not a word to sentence-case
    else:
        shown = text[:1].upper() + text[1:].lower() if text.isupper() or text.islower() else text
    return f"[{style}]{shown}[/]" if style else shown


def heading(text: str) -> str:
    """Section heading markup: ink, bold. Gold is reserved for the report's subject line."""
    return f"[bold]{text}[/]"


class Table(_RichTable):
    """House table: no box, a single rule under a muted sentence-case header, left-aligned title.

    Pass ``numeric`` with the headers whose cells are figures; those columns are right-aligned
    and never wrap, so digits line up.
    """

    def __init__(self, *args, numeric: Iterable[str] = (), **kwargs):
        kwargs.setdefault("box", box.SIMPLE_HEAD)
        kwargs.setdefault("show_edge", False)
        kwargs.setdefault("pad_edge", False)
        kwargs.setdefault("header_style", "muted")
        kwargs.setdefault("title_style", "bold")
        kwargs.setdefault("title_justify", "left")
        self._numeric = set(numeric)  # before super(): rich calls add_column() for headers passed positionally
        super().__init__(*args, **kwargs)

    def add_column(self, header="", *args, **kwargs):  # type: ignore[override]
        if header in self._numeric:
            kwargs.setdefault("justify", "right")
            kwargs.setdefault("no_wrap", True)
        return super().add_column(header, *args, **kwargs)


def kv_table(title: str | None, rows: Iterable[tuple[str, object]]) -> Table:
    """Two-column label/value table (labels muted)."""
    t = Table(title=title, show_header=False)
    t.add_column("label", style="label")
    t.add_column("value")
    for k, v in rows:
        t.add_row(k, str(v))
    return t


def error(msg: str) -> None:
    console.print(f"[negative]Error:[/] {msg}")


def humanize(name: str) -> str:
    """snake_case column key -> sentence-case header ("contract_size" -> "Contract size")."""
    s = name.replace("_", " ")
    return s[:1].upper() + s[1:]
