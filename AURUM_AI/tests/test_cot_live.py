"""Optional live CFTC test - NOT part of the normal run. Enable with:  $env:AURUM_LIVE_CFTC="1"; python -m pytest -m live

Downloads the current-year official history file for each family (a few MB) and checks that the mapped
contracts are present and parse without problems. Never writes to the configured database.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from aurum.cot.client import CftcClient
from aurum.cot.parsers import parse_record
from cot_helpers import cot_cfg

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("AURUM_LIVE_CFTC") != "1", reason="set AURUM_LIVE_CFTC=1 to run")]


def test_live_cftc_recent_reports(tmp_path):
    cfg = cot_cfg()
    client = CftcClient(cfg, tmp_path)
    end = date.today()
    for ds in cfg.datasets.values():
        codes = {m.cftc_contract_code for m in cfg.mappings_for(report_type=ds.key)}
        res = client.fetch(ds, codes, end - timedelta(days=60), end)
        assert res.records, f"no live records for {ds.key}"
        parsed = [parse_record(ds.key, r) for r in res.records]
        assert {p.contract_code for p in parsed} == codes
        assert all(not [x for x in p.problems if x["severity"] == "error"] for p in parsed)
