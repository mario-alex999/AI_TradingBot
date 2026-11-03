"""Central-bank meeting schedules read from the banks' OWN published calendars.

``python -m aurum.cli intelligence meetings-check`` compares the dates in ``config/central_banks.yaml`` with the
official pages and reports differences; ``meetings-update`` writes the official dates to
``config/central_banks.meetings.yaml``, which the configuration loader merges in (additively). Nothing is inferred:
a bank whose page cannot be read or parsed is reported as such and its configured dates stay as they are.

| bank | official source                                                                 | decision day           |
|------|---------------------------------------------------------------------------------|------------------------|
| fed  | federalreserve.gov/monetarypolicy/fomccalendars.htm ("2027 FOMC Meetings")      | 2nd day of the meeting |
| ecb  | ecb.europa.eu meeting calendar ("monetary policy meeting ... (Day 2)")          | Day 2                  |
| boe  | bankofengland.co.uk/monetary-policy/upcoming-mpc-dates ("2027 confirmed dates") | announcement date      |
| rba  | rba.gov.au board-meeting-schedules (Monetary Policy Board "D1–D2 Month")        | 2nd day                |
| boc  | bankofcanada.ca key-interest-rate page ("Schedule for 2027")                    | announcement date      |
| boj  | boj.or.jp MPM schedule ("Jan. 21 (Thurs.), 22 (Fri.)")                          | 2nd day                |
| snb  | snb.ch official iCal calendars ("Monetary policy assessment ... (press release)")| announcement date/time |

RBNZ is not supported: rbnz.govt.nz refuses automated clients and AURUM does not circumvent that.
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from aurum.market_intelligence.http import HttpClient, ProviderError
from aurum.market_intelligence.official_calendars import ics_start, parse_ics

UTC = timezone.utc
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
MON_RE = "January|February|March|April|May|June|July|August|September|October|November|December"
ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "aug": 8,
        "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
SOURCES = {
    "fed": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "ecb": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
    "boe": "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates",
    "rba": "https://www.rba.gov.au/schedules-events/board-meeting-schedules.html",
    "boc": "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/",
    "boj": "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm",
    "snb": ["https://www.snb.ch/public/ical/calendar/en/872f3023-70ea-42a9-8c27-524da3533fb7.ics",
            "https://www.snb.ch/public/ical/calendar/en/ac3d067c-1b93-475e-9d22-56045798603d.ics"],
}
OVERLAY = Path(__file__).resolve().parents[2] / "config" / "central_banks.meetings.yaml"


def page_text(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", html))).replace("–", "-")


def _year_sections(text: str, header: str) -> list[tuple[int, str]]:
    """Split ``text`` at headers matching ``header`` (with a (\\d{4}) group) -> [(year, section_text)]."""
    marks = [(int(m.group(1)), m.end()) for m in re.finditer(header, text)]
    out = []
    for i, (y, pos) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        out.append((y, text[pos:end]))
    return out


def parse_fed(html: str) -> list[date]:
    out = []
    for year, sec in _year_sections(page_text(html), r"(\d{4}) FOMC Meetings"):
        sec = re.split(r"Back to Top|\* Meeting associated|Note:", sec)[0]
        mon = rf"{MON_RE}|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
        for m in re.finditer(rf"\b({mon})(?:/({mon}))? (\d{{1,2}})-(?:({mon}) )?(\d{{1,2}})\b(?! \()", sec):
            name = (m.group(4) or m.group(2) or m.group(1)).lower()
            out.append(date(year, MONTHS.get(name) or ABBR[name], int(m.group(5))))
    return sorted(set(out))


def parse_ecb(html: str) -> list[date]:
    text = page_text(html)
    return sorted({datetime.strptime(m.group(1), "%d/%m/%Y").date() for m in re.finditer(
        r"(\d{2}/\d{2}/\d{4}) Governing Council of the ECB: monetary policy meeting[^()]{0,60}\(Day 2\)", text)})


def parse_boe(html: str) -> list[date]:
    out = []
    for year, sec in _year_sections(page_text(html), r"(\d{4}) (?:confirmed|provisional) dates"):
        for m in re.finditer(rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday) (\d{{1,2}}) ({MON_RE})", sec):
            out.append(date(year, MONTHS[m.group(2).lower()], int(m.group(1))))
    return sorted(set(out))


def parse_rba(html: str) -> list[date]:
    out = []
    for year, sec in _year_sections(page_text(html), r"Board meeting schedules (\d{4})"):
        for m in re.finditer(rf"(\d{{1,2}})(?: ({MON_RE}))?-(\d{{1,2}}) ({MON_RE})", sec):
            out.append(date(year, MONTHS[m.group(4).lower()], int(m.group(3))))
    return sorted(set(out))


def parse_boc(html: str) -> list[date]:
    out = []
    for year, sec in _year_sections(page_text(html), r"Schedule for (\d{4}) Dates Publications"):
        for m in re.finditer(rf"({MON_RE}) (\d{{1,2}}) Interest rate announcement", sec):
            out.append(date(year, MONTHS[m.group(1).lower()], int(m.group(2))))
    return sorted(set(out))


def parse_boj(html: str) -> list[date]:
    out = []
    for year, sec in _year_sections(page_text(html), r"(\d{4}) Table : \d{4} Date of MPM"):
        sec = sec.split("Note:")[0]
        for m in re.finditer(r"([A-Z][a-z]{2,4})\.? (\d{1,2}) \(\w+\.\), (?:([A-Z][a-z]{2,4})\.? )?(\d{1,2}) \(\w+\.\)",
                             sec):
            month = ABBR.get((m.group(3) or m.group(1)).lower())
            if month:
                out.append(date(year, month, int(m.group(4))))
    return sorted(set(out))


def parse_snb(ics_texts: list[str]) -> list[date]:
    out = set()
    for text in ics_texts:
        for ev in parse_ics(text):
            summary = ev.get("SUMMARY", ({}, ""))[1]
            if re.match(r"Monetary policy assessment of .*\(press release\)", summary):
                st = ics_start(ev.get("DTSTART"))
                if st and isinstance(st[0], datetime):
                    from aurum.market_intelligence.config import zone
                    out.add(st[0].astimezone(zone(st[1] or "Europe/Zurich")).date())
    return sorted(out)


PARSERS: dict[str, Callable[..., list[date]]] = {"fed": parse_fed, "ecb": parse_ecb, "boe": parse_boe,
                                                  "rba": parse_rba, "boc": parse_boc, "boj": parse_boj}


def fetch_official(bank: str, http: HttpClient) -> list[date]:
    src = SOURCES.get(bank)
    if src is None:
        raise ProviderError(f"No machine-readable official schedule source for '{bank}'", kind="config")
    if bank == "snb":
        return parse_snb([http.get(u).text for u in src])
    dates = PARSERS[bank](http.get(src).text)
    if not dates:
        raise ProviderError(f"{bank}: no meeting dates found on the official page (layout changed?)", kind="parse")
    return dates


def compare(configured: list[str], official: list[date]) -> dict[str, Any]:
    conf = {date.fromisoformat(str(d)) for d in configured or []}
    off = set(official)
    years = {d.year for d in off}
    # a configured date is "not on page" only for years the page lists (pages often drop past years)
    return {"official": sorted(d.isoformat() for d in off), "configured": sorted(d.isoformat() for d in conf),
            "missing_from_config": sorted(d.isoformat() for d in off - conf),
            "not_on_official_page": sorted(d.isoformat() for d in conf - off if d.year in years
                                           and d >= min(off)),
            "years_on_page": sorted(years)}


def check(banks_cfg: dict[str, Any], http: HttpClient | None = None, today: date | None = None) -> dict[str, Any]:
    http = http or HttpClient(timeout=40, max_retries=2)
    today = today or datetime.now(UTC).date()
    out = {}
    for bank, b in ((banks_cfg or {}).get("banks") or {}).items():
        row: dict[str, Any] = {"bank": bank, "source": SOURCES.get(bank)}
        try:
            row.update({"status": "ok", **compare(b.get("meetings") or [], fetch_official(bank, http))})
        except ProviderError as exc:
            row.update({"status": "unavailable" if exc.kind == "config" else "failed", "error": str(exc)[:300],
                        "configured": sorted(str(d) for d in b.get("meetings") or [])})
        row["staleness"] = staleness(b, today)
        out[bank] = row
    return out


def staleness(bank_cfg: dict[str, Any], today: date, warn_days: int = 120) -> dict[str, Any]:
    """Warn when the configured schedule runs out within ``warn_days`` (or has none)."""
    future = sorted(date.fromisoformat(str(d)) for d in bank_cfg.get("meetings") or []
                    if date.fromisoformat(str(d)) >= today)
    if not bank_cfg.get("meetings"):
        return {"status": "none", "message": "No meeting dates configured."}
    if not future:
        return {"status": "expired", "message": "All configured meetings are in the past."}
    left = (future[-1] - today).days
    return {"status": "ok" if left > warn_days else "running_out", "last_configured": future[-1].isoformat(),
            "days_left": left, "remaining": len(future),
            "message": None if left > warn_days else
            f"Configured meetings end on {future[-1].isoformat()}; run `intelligence meetings-update`."}


def write_overlay(result: dict[str, Any], path: Path = OVERLAY, now: datetime | None = None) -> Path:
    """Persist official dates (only banks that were read successfully) for the config loader to merge in."""
    now = now or datetime.now(UTC)
    data = {"generated_at": now.isoformat(timespec="seconds"),
            "note": "Generated by `python -m aurum.cli intelligence meetings-update` from the banks' official "
                    "calendars. Dates are merged into central_banks.yaml (union). Do not edit by hand.",
            "banks": {b: {"meetings": r["official"], "source": r["source"], "retrieved_at": now.date().isoformat()}
                      for b, r in result.items() if r.get("status") == "ok" and r.get("official")}}
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def merge_overlay(banks: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Union of configured and official (overlay) dates per bank; the overlay never removes a configured date."""
    for bank, o in ((overlay or {}).get("banks") or {}).items():
        b = (banks.get("banks") or {}).get(bank)
        if b is None:
            continue
        merged = sorted({str(d) for d in b.get("meetings") or []} | {str(d) for d in o.get("meetings") or []})
        b["meetings"] = merged
        b["meetings_overlay"] = {"source": o.get("source"), "retrieved_at": o.get("retrieved_at")}
    return banks
