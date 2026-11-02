"""CFTC data client.

Two official CFTC distribution channels for the same COT publication:

* :class:`SocrataSource` - the Public Reporting Environment API (``/resource/<dataset_id>.json``) with SoQL
  filtering (contract codes, date range), ordering, ``$limit/$offset`` pagination.
* :class:`FilesSource` - the official compressed history files on www.cftc.gov (``deacot2025.zip`` ...), which
  the CFTC updates weekly for the current year. Used when the API is unreachable/blocked (``source: auto``) or
  when forced. Downloaded archives are kept under ``data/raw/cot`` for traceability.

Both return plain ``dict`` records with the CFTC's original field names. Shared HTTP behaviour: timeouts,
retries with exponential backoff on connection errors / timeouts / HTTP 5xx, ``Retry-After`` on HTTP 429,
no retry on other 4xx, JSON/shape validation and structured logging.
"""
from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import httpx
import pandas as pd

from aurum.cot.config import CotConfig, DatasetConfig, SourceConfig
from aurum.cot.fields import META_FIELDS, norm

log = logging.getLogger(__name__)
USER_AGENT = "AURUM-AI/0.2 (COT research module; read-only)"

# Used by SocrataSource.verify_dataset: words the portal's dataset title must contain per report family, and the
# columns the parsers depend on.
FAMILY_TITLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "legacy": ("legacy",),
    "tff": ("tff", "traders in financial futures", "financial futures"),
    "disaggregated": ("disaggregated",),
}
REQUIRED_API_COLUMNS = frozenset({"report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "open_interest_all",
                                  "market_and_exchange_names"})


class CftcError(RuntimeError):
    """Base error for CFTC retrieval problems."""


class CftcHTTPError(CftcError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CftcRateLimited(CftcHTTPError):
    pass


class CftcResponseError(CftcError):
    """Response was received but is not valid JSON / not the expected shape."""


@dataclass
class FetchResult:
    records: list[dict[str, Any]]
    source: str                 # socrata | files
    requests: int
    detail: dict[str, Any]


class HttpFetcher:
    """httpx wrapper with retry/backoff. ``sleep`` is injectable so tests run instantly."""

    def __init__(self, cfg: SourceConfig, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, headers: dict[str, str] | None = None):
        self.cfg = cfg
        self.sleep = sleep
        self.client = client or httpx.Client(timeout=cfg.timeout_seconds, follow_redirects=True)
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.requests = 0

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            self.requests += 1
            try:
                resp = self.client.get(url, params=params, headers=self.headers, timeout=self.cfg.timeout_seconds)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > self.cfg.max_retries:
                    raise CftcHTTPError(f"{type(exc).__name__} contacting {url}: {exc}") from exc
                delay = self.cfg.backoff_seconds * 2 ** (attempt - 1)
                log.warning("CFTC request failed - retrying", extra={"url": url, "error": type(exc).__name__,
                                                                      "attempt": attempt, "delay_s": delay})
                self.sleep(delay)
                continue
            if resp.status_code == 429:
                if attempt > self.cfg.max_retries:
                    raise CftcRateLimited(f"Rate limited by {url} (HTTP 429) after {attempt} attempts", 429)
                delay = _retry_after(resp) or self.cfg.backoff_seconds * 2 ** (attempt - 1)
                log.warning("CFTC rate limit - backing off", extra={"url": url, "delay_s": delay, "attempt": attempt})
                self.sleep(delay)
                continue
            if resp.status_code >= 500:
                if attempt > self.cfg.max_retries:
                    raise CftcHTTPError(f"HTTP {resp.status_code} from {url}", resp.status_code)
                delay = self.cfg.backoff_seconds * 2 ** (attempt - 1)
                log.warning("CFTC server error - retrying", extra={"url": url, "status": resp.status_code,
                                                                   "attempt": attempt, "delay_s": delay})
                self.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise CftcHTTPError(f"HTTP {resp.status_code} from {url}", resp.status_code)
            return resp


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("Retry-After")
    try:
        return max(0.0, float(v)) if v is not None else None
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------- Socrata
class SocrataSource:
    name = "socrata"

    def __init__(self, cfg: SourceConfig, fetcher: HttpFetcher | None = None):
        token = os.environ.get(cfg.app_token_env) if cfg.app_token_env else None
        self.cfg = cfg
        self.fetcher = fetcher or HttpFetcher(cfg, headers={"X-App-Token": token} if token else None)

    def _endpoint(self, ds: DatasetConfig) -> str:
        return ds.endpoint(self.cfg.base_url)

    def probe(self, ds: DatasetConfig) -> dict[str, Any]:
        """Cheap reachability check: newest record date for the dataset."""
        resp = self.fetcher.get(self._endpoint(ds), params={
            "$select": "max(report_date_as_yyyy_mm_dd) AS latest", "$limit": 1})
        rows = _json_list(resp)
        return {"reachable": True, "latest": (rows[0] or {}).get("latest") if rows else None}

    def verify_dataset(self, ds: DatasetConfig) -> dict[str, Any]:
        """Check a configured dataset ID against the portal's own metadata (/api/views/{id}.json).

        Verified only when the dataset is published by the CFTC portal, its title names the expected report
        family and the futures-only scope, and it carries the columns the parsers rely on. Never raises for HTTP
        failures - the result says why verification could not be completed."""
        url = f"{self.cfg.base_url.rstrip('/')}/api/views/{ds.dataset_id}.json"
        try:
            resp = self.fetcher.get(url)
            meta = resp.json()
        except CftcError as exc:
            return {"dataset": ds.key, "dataset_id": ds.dataset_id, "status": "unreachable", "reason": str(exc)}
        except ValueError as exc:
            return {"dataset": ds.key, "dataset_id": ds.dataset_id, "status": "unreachable",
                    "reason": f"invalid JSON metadata: {exc}"}
        if not isinstance(meta, dict):
            return {"dataset": ds.key, "dataset_id": ds.dataset_id, "status": "mismatch",
                    "reason": "metadata is not a JSON object"}
        title = str(meta.get("name") or "")
        columns = {str(c.get("fieldName", "")) for c in meta.get("columns") or [] if isinstance(c, dict)}
        problems = []
        if meta.get("id") != ds.dataset_id:
            problems.append(f"portal returned id {meta.get('id')!r}")
        low = title.lower()
        if not any(k in low for k in FAMILY_TITLE_KEYWORDS.get(ds.key, (ds.key,))):
            problems.append(f"title {title!r} does not name the {ds.key} report family")
        if ds.report_scope == "futures_only" and ("combined" in low or "futures only" not in low.replace("-", " ")):
            problems.append(f"title {title!r} is not the futures-only report")
        missing = sorted(REQUIRED_API_COLUMNS - columns)
        if missing:
            problems.append(f"missing columns {missing}")
        return {"dataset": ds.key, "dataset_id": ds.dataset_id, "title": title,
                "status": "mismatch" if problems else "verified", "reason": "; ".join(problems) or None,
                "rows_updated_at": meta.get("rowsUpdatedAt"), "source": url}

    def fetch(self, ds: DatasetConfig, codes: Iterable[str], start: date, end: date,
              progress: Callable[[str], None] | None = None) -> FetchResult:
        codes = sorted(set(codes))
        code_list = ",".join(f"'{c}'" for c in codes)
        where = (f"cftc_contract_market_code in({code_list}) AND report_date_as_yyyy_mm_dd >= '{start.isoformat()}' "
                 f"AND report_date_as_yyyy_mm_dd <= '{end.isoformat()}T23:59:59'")
        out: list[dict[str, Any]] = []
        offset, pages = 0, 0
        while True:
            resp = self.fetcher.get(self._endpoint(ds), params={
                "$where": where, "$order": "report_date_as_yyyy_mm_dd ASC, id ASC",
                "$limit": self.cfg.page_size, "$offset": offset})
            page = _json_list(resp)
            pages += 1
            out.extend(page)
            if progress:
                progress(f"{ds.key}: page {pages} ({len(out)} records)")
            if len(page) < self.cfg.page_size:
                break
            offset += self.cfg.page_size
        return FetchResult(out, self.name, self.fetcher.requests, {"pages": pages, "where": where})


def _json_list(resp: httpx.Response) -> list[dict[str, Any]]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise CftcResponseError(f"Invalid JSON from {resp.request.url}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise CftcResponseError(f"Unexpected response shape from {resp.request.url}: expected a JSON array of objects")
    return data


# --------------------------------------------------------------------------------------------- history files
class FilesSource:
    name = "files"

    def __init__(self, cfg: SourceConfig, raw_dir: Path, fetcher: HttpFetcher | None = None,
                 sleep: Callable[[float], None] = time.sleep, today: Callable[[], date] = date.today):
        self.cfg = cfg
        self.raw_dir = raw_dir
        self.fetcher = fetcher or HttpFetcher(cfg)
        self.sleep = sleep
        self.today = today

    def files_for(self, ds: DatasetConfig, start: date, end: date) -> list[tuple[str, bool]]:
        """[(file name, refresh)] covering [start, end]; refresh=True for files that still change (current year)."""
        start = max(start, ds.history_start)
        out: list[tuple[str, bool]] = []
        last_hist = ds.history_file_last_year or 0
        if ds.history_file and start.year <= last_hist:
            out.append((ds.history_file, False))
        for y in range(max(start.year, last_hist + 1), end.year + 1):
            out.append((ds.yearly_file.format(year=y), y >= self.today().year - (1 if self.today().month == 1 else 0)))
        return out

    def probe(self, ds: DatasetConfig) -> dict[str, Any]:
        name = ds.yearly_file.format(year=self.today().year)
        resp = self.fetcher.get(f"{self.cfg.base_url.rstrip('/')}/{name}")
        return {"reachable": True, "file": name, "bytes": len(resp.content)}

    def download(self, ds: DatasetConfig, name: str, refresh: bool) -> Path:
        path = self.raw_dir / ds.key / name
        if path.exists() and not refresh:
            return path
        resp = self.fetcher.get(f"{self.cfg.base_url.rstrip('/')}/{name}")
        if not zipfile.is_zipfile(io.BytesIO(resp.content)):
            raise CftcResponseError(f"{name} is not a valid zip archive")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(path)
        if self.cfg.polite_delay_seconds:
            self.sleep(self.cfg.polite_delay_seconds)
        return path

    @staticmethod
    def read_archive(path: Path) -> pd.DataFrame:
        with zipfile.ZipFile(path) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith((".txt", ".csv"))]
            if not members:
                raise CftcResponseError(f"{path.name} contains no .txt/.csv file")
            frames = []
            for m in members:
                with zf.open(m) as fh:
                    frames.append(pd.read_csv(fh, dtype=str, keep_default_na=False, low_memory=False))
        return pd.concat(frames, ignore_index=True)

    def fetch(self, ds: DatasetConfig, codes: Iterable[str], start: date, end: date,
              progress: Callable[[str], None] | None = None) -> FetchResult:
        codes = {c.strip() for c in codes}
        out: list[dict[str, Any]] = []
        files = self.files_for(ds, start, end)
        for name, refresh in files:
            path = self.download(ds, name, refresh)
            df = self.read_archive(path)
            cols = {norm(c): c for c in df.columns}
            code_col = next((cols[a] for a in META_FIELDS["contract_code"] if a in cols), None)
            date_col = next((cols[a] for a in META_FIELDS["report_date"] if a in cols), None)
            if code_col is None or date_col is None:
                raise CftcResponseError(f"{name}: contract-code or report-date column missing (schema change?)")
            dates = pd.to_datetime(df[date_col].str.strip().str[:10], errors="coerce").dt.date
            mask = df[code_col].str.strip().isin(codes) & (dates >= start) & (dates <= end)
            sub = df[mask]
            for rec in sub.to_dict(orient="records"):
                rec["_source_file"] = name
                out.append(rec)
            if progress:
                progress(f"{ds.key}: {name} -> {len(sub)} records")
        return FetchResult(out, self.name, self.fetcher.requests, {"files": [f for f, _ in files]})


# --------------------------------------------------------------------------------------------- facade
class CftcClient:
    """Chooses the source per ``cot.yaml`` (``auto`` = Socrata, falling back to the history files)."""

    def __init__(self, cfg: CotConfig, raw_dir: Path, socrata: SocrataSource | None = None,
                 files: FilesSource | None = None):
        self.cfg = cfg
        self.socrata = socrata or SocrataSource(cfg.socrata)
        self.files = files or FilesSource(cfg.files, raw_dir)
        self.last_fallback_reason: str | None = None

    def fetch(self, ds: DatasetConfig, codes: Iterable[str], start: date, end: date,
              progress: Callable[[str], None] | None = None) -> FetchResult:
        codes = list(codes)
        if self.cfg.source in ("auto", "socrata"):
            try:
                return self.socrata.fetch(ds, codes, start, end, progress)
            except CftcError as exc:
                if self.cfg.source == "socrata":
                    raise
                self.last_fallback_reason = f"Socrata API unavailable ({exc}); used official CFTC history files"
                log.warning("CFTC API unavailable - falling back to history files",
                            extra={"dataset": ds.key, "error": str(exc)})
        return self.files.fetch(ds, codes, start, end, progress)

    def check(self) -> list[dict[str, Any]]:
        """Connectivity per dataset and source (never raises)."""
        rows = []
        for ds in self.cfg.datasets.values():
            for src in (self.socrata, self.files):
                try:
                    info = src.probe(ds)
                    rows.append({"dataset": ds.key, "source": src.name, "ok": True, **info})
                except Exception as exc:  # noqa: BLE001 - report every failure kind
                    rows.append({"dataset": ds.key, "source": src.name, "ok": False, "error": str(exc)})
        return rows
