"""Load ``config/cot.yaml`` into typed, immutable configuration objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aurum.config import PROJECT_ROOT

COT_CONFIG_PATH = PROJECT_ROOT / "config" / "cot.yaml"
REPORT_TYPES = ("legacy", "tff", "disaggregated")


class CotConfigError(ValueError):
    """Invalid or inconsistent config/cot.yaml."""


@dataclass(frozen=True)
class SourceConfig:
    base_url: str
    timeout_seconds: float = 30
    max_retries: int = 4
    backoff_seconds: float = 2.0
    page_size: int = 5000
    polite_delay_seconds: float = 0.0
    app_token_env: str | None = None


@dataclass(frozen=True)
class DatasetConfig:
    key: str                         # legacy | tff | disaggregated
    name: str
    report_scope: str
    dataset_id: str
    dataset_id_status: str
    history_start: date
    yearly_file: str
    history_file: str | None
    history_file_last_year: int | None
    description: str

    def endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/resource/{self.dataset_id}.json"


@dataclass(frozen=True)
class ContractMapping:
    aurum_symbol: str
    report_type: str
    cftc_contract_name: str
    cftc_contract_code: str
    exchange: str
    contract_description: str
    currency: str
    mapping_status: str
    verification_source: str
    verified_at: date | None
    primary_category: str


@dataclass(frozen=True)
class ClassificationConfig:
    extremely_bullish: float = 90
    strong_bullish: float = 75
    moderately_bullish: float = 60
    moderately_bearish: float = 40
    strong_bearish: float = 25
    extremely_bearish: float = 10
    require_direction_for_extremes: bool = True


@dataclass(frozen=True)
class DivergenceConfig:
    price_timeframe: str = "D1"
    window_reports: int = 4
    extreme_lookback_reports: int = 26
    min_price_change_pct: float = 0.5


@dataclass(frozen=True)
class AnalyticsConfig:
    percentile_lookbacks_weeks: dict[str, int] = field(
        default_factory=lambda: {"12m": 52, "24m": 104, "36m": 156, "48m": 208})
    cot_index_lookback_weeks: int = 156
    zscore_lookback_weeks: int = 156
    min_history_weeks: int = 26
    significant_change_z: float = 2.0
    unusual_percentile: tuple[float, float] = (5.0, 95.0)
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    divergence: DivergenceConfig = field(default_factory=DivergenceConfig)
    stale_after_days: int = 10


@dataclass(frozen=True)
class ReleaseConfig:
    timezone: str = "America/New_York"
    time: time = time(15, 30)
    overrides: dict[date, date] = field(default_factory=dict)
    uncertain_ranges: tuple[tuple[date, date], ...] = ()


@dataclass(frozen=True)
class CotConfig:
    source: str
    socrata: SourceConfig
    files: SourceConfig
    datasets: dict[str, DatasetConfig]
    mappings: tuple[ContractMapping, ...]
    release: ReleaseConfig
    analytics: AnalyticsConfig

    def mappings_for(self, symbol: str | None = None, report_type: str | None = None) -> list[ContractMapping]:
        return [m for m in self.mappings
                if (symbol is None or m.aurum_symbol == symbol.upper())
                and (report_type is None or m.report_type == report_type)]

    @property
    def symbols(self) -> list[str]:
        return list(dict.fromkeys(m.aurum_symbol for m in self.mappings))


def _d(v: Any) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def parse_cot_config(raw: dict[str, Any]) -> CotConfig:
    source = str(raw.get("source", "auto")).lower()
    if source not in ("auto", "socrata", "files"):
        raise CotConfigError(f"source must be auto, socrata or files (got '{source}')")
    s, f = raw.get("socrata") or {}, raw.get("files") or {}
    socrata = SourceConfig(base_url=s.get("base_url", "https://publicreporting.cftc.gov"),
                           timeout_seconds=float(s.get("timeout_seconds", 30)), max_retries=int(s.get("max_retries", 5)),
                           backoff_seconds=float(s.get("backoff_seconds", 2.0)), page_size=int(s.get("page_size", 5000)),
                           app_token_env=s.get("app_token_env"))
    files = SourceConfig(base_url=f.get("base_url", "https://www.cftc.gov/files/dea/history"),
                         timeout_seconds=float(f.get("timeout_seconds", 120)), max_retries=int(f.get("max_retries", 4)),
                         backoff_seconds=float(f.get("backoff_seconds", 3.0)),
                         polite_delay_seconds=float(f.get("polite_delay_seconds", 1.0)))
    datasets: dict[str, DatasetConfig] = {}
    for key, d in (raw.get("datasets") or {}).items():
        if key not in REPORT_TYPES:
            raise CotConfigError(f"Unknown dataset key '{key}' (expected one of {REPORT_TYPES})")
        datasets[key] = DatasetConfig(
            key=key, name=d["name"], report_scope=d.get("report_scope", "futures_only"), dataset_id=d["dataset_id"],
            dataset_id_status=d.get("dataset_id_status", "unverified"), history_start=_d(d["history_start"]),
            yearly_file=d["yearly_file"], history_file=d.get("history_file"),
            history_file_last_year=d.get("history_file_last_year"), description=d.get("description", ""))
    mappings = []
    seen: set[tuple[str, str]] = set()
    for m in raw.get("mappings") or []:
        rt = m["report_type"]
        if rt not in datasets:
            raise CotConfigError(f"Mapping {m.get('aurum_symbol')} uses undefined report_type '{rt}'")
        key = (str(m["aurum_symbol"]).upper(), rt)
        if key in seen:   # one contract per instrument and report family: never double-count
            raise CotConfigError(f"Duplicate mapping for {key}: only one contract per instrument and report family")
        seen.add(key)
        mappings.append(ContractMapping(
            aurum_symbol=key[0], report_type=rt, cftc_contract_name=m["cftc_contract_name"],
            cftc_contract_code=str(m["cftc_contract_code"]).strip(), exchange=m.get("exchange", ""),
            contract_description=m.get("contract_description", ""), currency=m.get("currency", "USD"),
            mapping_status=m.get("mapping_status", "unverified"), verification_source=m.get("verification_source", ""),
            verified_at=_d(m.get("verified_at")), primary_category=m["primary_category"]))
    r = raw.get("release") or {}
    release = ReleaseConfig(
        timezone=r.get("timezone", "America/New_York"), time=time.fromisoformat(str(r.get("time", "15:30"))),
        overrides={_d(k): _d(v) for k, v in (r.get("overrides") or {}).items()},
        uncertain_ranges=tuple((_d(a), _d(b)) for a, b in (r.get("uncertain_ranges") or [])))
    a = raw.get("analytics") or {}
    c, dv = a.get("classification") or {}, a.get("divergence") or {}
    analytics = AnalyticsConfig(
        percentile_lookbacks_weeks={str(k): int(v) for k, v in (a.get("percentile_lookbacks_weeks") or
                                                               {"12m": 52, "24m": 104, "36m": 156, "48m": 208}).items()},
        cot_index_lookback_weeks=int(a.get("cot_index_lookback_weeks", 156)),
        zscore_lookback_weeks=int(a.get("zscore_lookback_weeks", 156)),
        min_history_weeks=int(a.get("min_history_weeks", 26)),
        significant_change_z=float(a.get("significant_change_z", 2.0)),
        unusual_percentile=tuple(float(x) for x in a.get("unusual_percentile", [5, 95])),
        classification=ClassificationConfig(**{k: c[k] for k in c}),
        divergence=DivergenceConfig(**{k: dv[k] for k in dv}),
        stale_after_days=int(a.get("stale_after_days", 10)))
    cl = analytics.classification
    order = [cl.extremely_bearish, cl.strong_bearish, cl.moderately_bearish, cl.moderately_bullish,
             cl.strong_bullish, cl.extremely_bullish]
    if order != sorted(order):
        raise CotConfigError("classification thresholds must increase from extremely_bearish to extremely_bullish")
    return CotConfig(source=source, socrata=socrata, files=files, datasets=datasets, mappings=tuple(mappings),
                     release=release, analytics=analytics)


def load_cot_config(path: Path | None = None) -> CotConfig:
    p = path or COT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"COT configuration not found: {p}")
    return parse_cot_config(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def set_dataset_id_status(dataset_id: str, status: str, note: str, path: Path | None = None) -> bool:
    """Rewrite the ``dataset_id_status`` line that follows ``dataset_id: <dataset_id>`` in cot.yaml, keeping every
    other line (and all comments) untouched. Returns True when a line was changed."""
    import re
    p = path or COT_CONFIG_PATH
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.fullmatch(rf"\s*dataset_id:\s*['\"]?{re.escape(dataset_id)}['\"]?\s*(#.*)?\n?", line):
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"(\s*)dataset_id_status:", lines[j])
                if m:
                    new = f"{m.group(1)}dataset_id_status: {status}   # {note}\n"
                    changed = lines[j] != new
                    lines[j] = new
                    p.write_text("".join(lines), encoding="utf-8")
                    get_cot_config.cache_clear()
                    return changed
    return False


@lru_cache(maxsize=1)
def get_cot_config() -> CotConfig:
    return load_cot_config()
