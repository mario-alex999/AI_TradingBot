"""CFTC client: Socrata API + official history files, all HTTP mocked (no live requests)."""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from aurum.cot.client import (CftcClient, CftcHTTPError, CftcRateLimited, CftcResponseError, FilesSource, HttpFetcher,
                              SocrataSource)
from aurum.cot.config import SourceConfig
from cot_helpers import cot_cfg, make_record, series, zip_bytes


def fetcher(handler, **cfg):
    sleeps: list[float] = []
    c = SourceConfig(base_url="https://example.test", max_retries=cfg.pop("max_retries", 3), backoff_seconds=1.0,
                     page_size=cfg.pop("page_size", 2), timeout_seconds=5)
    f = HttpFetcher(c, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    return c, f, sleeps


def socrata(handler, **cfg):
    c, f, sleeps = fetcher(handler, **cfg)
    return SocrataSource(c, fetcher=f), sleeps


DS = cot_cfg().datasets["legacy"]


def test_valid_response_and_query_parameters():
    seen = {}

    def h(req: httpx.Request):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json=[make_record("legacy", "099741", date(2026, 1, 6), socrata=True)])
    src, _ = socrata(h, page_size=10)
    res = src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 2, 1))
    assert len(res.records) == 1 and res.source == "socrata"
    assert "cftc_contract_market_code in('099741')" in seen["$where"]
    assert "report_date_as_yyyy_mm_dd >= '2026-01-01'" in seen["$where"]          # date filtering
    assert "report_date_as_yyyy_mm_dd <= '2026-02-01T23:59:59'" in seen["$where"]
    assert seen["$order"].startswith("report_date_as_yyyy_mm_dd ASC")            # sorting
    assert seen["$limit"] == "10" and seen["$offset"] == "0"


def test_empty_response():
    src, _ = socrata(lambda req: httpx.Response(200, json=[]))
    assert src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 2, 1)).records == []


def test_pagination_follows_offset_until_short_page():
    recs = series("legacy", "099741", date(2026, 1, 1), [1, 2, 3, 4, 5], socrata=True)
    offsets = []

    def h(req):
        off, lim = int(req.url.params["$offset"]), int(req.url.params["$limit"])
        offsets.append(off)
        return httpx.Response(200, json=recs[off:off + lim])
    src, _ = socrata(h, page_size=2)
    res = src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 3, 1))
    assert len(res.records) == 5 and offsets == [0, 2, 4] and res.detail["pages"] == 3


def test_server_error_is_retried_with_exponential_backoff():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=[])
    src, sleeps = socrata(h)
    src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 1, 2))
    assert calls["n"] == 3 and sleeps == [1.0, 2.0]


def test_http_error_not_retried_on_4xx():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(403, text="<html>403 Forbidden</html>")
    src, _ = socrata(h)
    with pytest.raises(CftcHTTPError) as e:
        src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 1, 2))
    assert e.value.status == 403 and calls["n"] == 1


def test_rate_limit_honours_retry_after_then_gives_up():
    src, sleeps = socrata(lambda req: httpx.Response(429, headers={"Retry-After": "7"}), max_retries=2)
    with pytest.raises(CftcRateLimited):
        src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 1, 2))
    assert sleeps == [7.0, 7.0]


def test_timeouts_and_connection_errors_retry_then_raise():
    def h(req):
        raise httpx.ConnectTimeout("timed out", request=req)
    src, sleeps = socrata(h, max_retries=2)
    with pytest.raises(CftcHTTPError, match="ConnectTimeout"):
        src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 1, 2))
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize("body", ["not json{", '{"a": 1}', "[1, 2]"])
def test_invalid_json_or_shape_is_rejected(body):
    src, _ = socrata(lambda req: httpx.Response(200, text=body, headers={"content-type": "application/json"}))
    with pytest.raises(CftcResponseError):
        src.fetch(DS, ["099741"], date(2026, 1, 1), date(2026, 1, 2))


# ------------------------------------------------------------------ history files
def files_source(handler, tmp_path, today=date(2026, 11, 2)):
    c, f, _ = fetcher(handler)
    return FilesSource(c, tmp_path, fetcher=f, sleep=lambda s: None, today=lambda: today)


def test_files_for_uses_history_archive_and_yearly_files():
    ds = cot_cfg().datasets["tff"]
    fs = files_source(lambda r: httpx.Response(200), None)
    names = fs.files_for(ds, date(2015, 3, 1), date(2026, 11, 2))
    assert names[0] == ("fin_fut_txt_2006_2016.zip", False)
    assert ("fut_fin_txt_2017.zip", False) in names and names[-1] == ("fut_fin_txt_2026.zip", True)
    # dataset-specific coverage: nothing before history_start
    assert fs.files_for(ds, date(1990, 1, 1), date(2006, 12, 31)) == [("fin_fut_txt_2006_2016.zip", False)]


def test_files_source_downloads_filters_and_caches(tmp_path):
    recs = series("legacy", "099741", date(2025, 12, 1), [1, 2, 3]) + series("legacy", "999999", date(2025, 12, 1), [9])
    calls = []

    def h(req):
        calls.append(req.url.path)
        return httpx.Response(200, content=zip_bytes(recs))
    fs = files_source(h, tmp_path)
    ds = cot_cfg().datasets["legacy"]
    res = fs.fetch(ds, ["099741"], date(2025, 12, 5), date(2025, 12, 31))
    assert res.source == "files" and len(res.records) == 2                     # code + date filtered
    assert all(r["_source_file"] == "deacot2025.zip" for r in res.records)
    assert (tmp_path / "legacy" / "deacot2025.zip").exists()                  # raw archive kept
    fs.fetch(ds, ["099741"], date(2025, 12, 5), date(2025, 12, 31))
    assert len(calls) == 1                                                    # closed year served from cache


def test_files_source_rejects_non_zip_and_schema_change(tmp_path):
    ds = cot_cfg().datasets["legacy"]
    fs = files_source(lambda r: httpx.Response(200, content=b"<html>maintenance</html>"), tmp_path)
    with pytest.raises(CftcResponseError, match="not a valid zip"):
        fs.fetch(ds, ["099741"], date(2025, 1, 1), date(2025, 2, 1))
    fs2 = files_source(lambda r: httpx.Response(200, content=zip_bytes([{"foo": 1, "bar": 2}])), tmp_path / "b")
    with pytest.raises(CftcResponseError, match="schema change"):
        fs2.fetch(ds, ["099741"], date(2025, 1, 1), date(2025, 2, 1))


def test_auto_source_falls_back_to_files_when_api_blocked(tmp_path):
    cfg = cot_cfg()
    api, _ = socrata(lambda r: httpx.Response(403))
    files = files_source(lambda r: httpx.Response(200, content=zip_bytes(series("legacy", "099741", date(2026, 1, 1), [5]))),
                         tmp_path)
    client = CftcClient(cfg, tmp_path, socrata=api, files=files)
    res = client.fetch(cfg.datasets["legacy"], ["099741"], date(2026, 1, 1), date(2026, 2, 1))
    assert res.source == "files" and len(res.records) == 1
    assert "HTTP 403" in client.last_fallback_reason


def test_forced_socrata_source_does_not_fall_back(tmp_path):
    import dataclasses
    cfg = dataclasses.replace(cot_cfg(), source="socrata")
    api, _ = socrata(lambda r: httpx.Response(403))
    client = CftcClient(cfg, tmp_path, socrata=api, files=files_source(lambda r: httpx.Response(200), tmp_path))
    with pytest.raises(CftcHTTPError):
        client.fetch(cfg.datasets["legacy"], ["099741"], date(2026, 1, 1), date(2026, 2, 1))


def test_check_reports_each_source_without_raising(tmp_path):
    cfg = cot_cfg()
    api, _ = socrata(lambda r: httpx.Response(403))
    files = files_source(lambda r: httpx.Response(200, content=b"PK"), tmp_path)
    rows = CftcClient(cfg, tmp_path, socrata=api, files=files).check()
    assert {(r["source"], r["ok"]) for r in rows} == {("socrata", False), ("files", True)}


# ------------------------------------------------------------------------------ dataset-ID verification
COLS = [{"fieldName": f} for f in ("report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "open_interest_all",
                                   "market_and_exchange_names", "noncomm_positions_long_all")]


def _meta_source(meta, status=200):
    def h(req: httpx.Request):
        assert req.url.path == f"/api/views/{DS.dataset_id}.json"
        return httpx.Response(status, json=meta)
    return socrata(h, max_retries=0)[0]


def test_verify_dataset_accepts_matching_portal_metadata():
    r = _meta_source({"id": DS.dataset_id, "name": "Legacy - Futures Only", "columns": COLS}).verify_dataset(DS)
    assert r["status"] == "verified" and r["reason"] is None and r["title"] == "Legacy - Futures Only"


@pytest.mark.parametrize("meta, why", [
    ({"id": DS.dataset_id, "name": "TFF - Futures Only", "columns": COLS}, "report family"),
    ({"id": DS.dataset_id, "name": "Legacy - Combined", "columns": COLS}, "futures-only"),
    ({"id": DS.dataset_id, "name": "Legacy - Futures Only", "columns": COLS[:2]}, "missing columns"),
    ({"id": "zzzz-zzzz", "name": "Legacy - Futures Only", "columns": COLS}, "portal returned id"),
])
def test_verify_dataset_rejects_mismatches(meta, why):
    r = _meta_source(meta).verify_dataset(DS)
    assert r["status"] == "mismatch" and why in r["reason"]


def test_verify_dataset_reports_blocked_portal_without_raising():
    r = _meta_source({}, status=403).verify_dataset(DS)
    assert r["status"] == "unreachable" and "403" in r["reason"]


def test_set_dataset_id_status_rewrites_only_the_target_line(tmp_path):
    from aurum.cot.config import set_dataset_id_status
    p = tmp_path / "cot.yaml"
    p.write_text("datasets:\n  legacy:\n    # keep me\n    dataset_id: 6dca-aqww\n    dataset_id_status: corroborated  # x\n"
                 "  tff:\n    dataset_id: gpe5-46if\n    dataset_id_status: corroborated\n", encoding="utf-8")
    assert set_dataset_id_status("gpe5-46if", "verified", "confirmed", path=p)
    text = p.read_text(encoding="utf-8")
    assert "    dataset_id_status: verified   # confirmed\n" in text
    assert "dataset_id_status: corroborated  # x" in text and "# keep me" in text
    assert not set_dataset_id_status("nope-nope", "verified", "x", path=p)
