"""CSV exports for Module 02. Every row carries ``data_status``: real | incomplete | unavailable.

Files go to ``data/exports/cot/`` and are never overwritten silently (a UTC timestamp is appended when the
file exists, unless ``overwrite=True``).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from aurum.cot.config import CotConfig
from aurum.cot.repository import CotRepository

KINDS = ("raw", "positions", "metrics", "analysis", "quality")


def _target(out_dir: Path, name: str, overwrite: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{name}.csv"
    if p.exists() and not overwrite:
        p = out_dir / f"{name}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    return p


def _write(df: pd.DataFrame, path: Path) -> Path:
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    tmp.replace(path)
    return path


def _symbol_of(cfg: CotConfig) -> dict[tuple[str, str], str]:
    return {(m.report_type, m.cftc_contract_code): m.aurum_symbol for m in cfg.mappings}


def raw_frame(repo: CotRepository, cfg: CotConfig, symbol: str | None = None) -> pd.DataFrame:
    from sqlalchemy import select
    from aurum.cot.models import CotReport
    q = select(CotReport).order_by(CotReport.report_type, CotReport.contract_code, CotReport.report_date)
    sym = _symbol_of(cfg)
    rows = []
    with repo.engine.connect() as c:
        for r in c.execute(q):
            d = dict(r._mapping)
            s = sym.get((d["report_type"], d["contract_code"]))
            if symbol and s != symbol:
                continue
            payload = d.pop("source_payload")
            rows.append({"aurum_symbol": s, **{k: d[k] for k in (
                "report_type", "contract_code", "contract_market_name", "report_date", "release_date",
                "release_timestamp", "release_method", "release_uncertain", "retrieval_timestamp", "source",
                "source_file", "source_record_id", "open_interest", "payload_hash")},
                "data_status": "real", "source_payload_json": json.dumps(payload, default=str)})
    return pd.DataFrame(rows)


def positions_frame(repo: CotRepository, cfg: CotConfig, symbol: str | None = None) -> pd.DataFrame:
    frames = []
    for (s, rt), mid in _mapping_ids(repo).items():
        if symbol and s != symbol:
            continue
        df = repo.positions_frame(mid)
        if df.empty:
            continue
        df.insert(0, "report_type", rt)
        df.insert(0, "aurum_symbol", s)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["data_status"] = out.apply(lambda r: "incomplete" if pd.isna(r["long_positions"]) or
                                       pd.isna(r["short_positions"]) else "real", axis=1)
    return out


def metrics_frame(repo: CotRepository, cfg: CotConfig, symbol: str | None = None) -> pd.DataFrame:
    ids = {v: k for k, v in _mapping_ids(repo).items()}
    df = repo.metrics_frame()
    if df.empty:
        return df
    df.insert(0, "aurum_symbol", df["contract_mapping_id"].map(lambda i: ids.get(i, (None,))[0]))
    if symbol:
        df = df[df["aurum_symbol"] == symbol]
    df["data_status"] = df.apply(lambda r: "incomplete" if pd.isna(r["historical_percentile_36m"]) else "real", axis=1)
    return df


def analysis_frame(reports: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for rep in reports:
        for f in rep["families"]:
            if not f.get("available"):
                rows.append({"instrument": rep["instrument"], "report_type": f["report_type"],
                             "futures_contract": f["contract"]["name"], "data_status": "unavailable",
                             "interpretation": f.get("reason")})
                continue
            for c in f["categories"]:
                rows.append({
                    "instrument": rep["instrument"], "report_type": f["report_type"],
                    "futures_contract": f["contract"]["name"], "contract_code": f["contract"]["code"],
                    "report_date": f["report_date"], "release_date": f["release_date"],
                    "freshness": f["freshness"]["status"], "participant_category": c["label"],
                    "long": c["long"], "short": c["short"], "spreading": c["spreading"], "net": c["net_position"],
                    "weekly_net_change": c["weekly_net_change"], "net_pct_oi": c["net_position_pct_oi"],
                    "percentile_12m": c["historical_percentile_12m"], "percentile_24m": c["historical_percentile_24m"],
                    "percentile_36m": c["historical_percentile_36m"], "percentile_48m": c["historical_percentile_48m"],
                    "cot_index": c["cot_index"], "z_score": c["z_score"],
                    "classification": c["positioning_strength"], "change_type": c["change_type"],
                    "confidence": f["confidence"]["level"],
                    "limitations": "; ".join(f["confidence"]["reasons"]) or None,
                    "interpretation": " ".join(f["statements"]["interpretation"]) if c["category"] == f["primary_category"] else None,
                    "overall_stance": rep["overall"]["stance"],
                    "data_status": "real" if c["historical_percentile_36m"] is not None else "incomplete",
                })
    return pd.DataFrame(rows)


def quality_frame(repo: CotRepository) -> pd.DataFrame:
    rows, _ = repo.issues(include_resolved=True, limit=100000)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["details"] = df["details"].map(lambda d: json.dumps(d, default=str) if d else None)
        df["data_status"] = "real"
    return df


def _mapping_ids(repo: CotRepository) -> dict[tuple[str, str], int]:
    return {(m["aurum_symbol"], m["report_type"]): m["id"] for m in repo.mappings()}


def export(kind: str, repo: CotRepository, cfg: CotConfig, out_dir: Path, symbol: str | None = None,
           overwrite: bool = False, analysis_builder: Callable[[], list[dict[str, Any]]] | None = None) -> tuple[Path, int]:
    if kind not in KINDS:
        raise ValueError(f"Unknown export '{kind}'. Choose from {KINDS}")
    sym = symbol.upper() if symbol else None
    if kind == "raw":
        df = raw_frame(repo, cfg, sym)
    elif kind == "positions":
        df = positions_frame(repo, cfg, sym)
    elif kind == "metrics":
        df = metrics_frame(repo, cfg, sym)
    elif kind == "quality":
        df = quality_frame(repo)
    else:
        if analysis_builder is None:
            raise ValueError("analysis export needs an analysis builder")
        df = analysis_frame(analysis_builder())
    name = f"cot_{kind}" + (f"_{sym}" if sym else "")
    if df.empty:
        df = pd.DataFrame([{"data_status": "unavailable", "note": "No data stored for this export."}])
    return _write(df, _target(out_dir, name, overwrite)), len(df)
