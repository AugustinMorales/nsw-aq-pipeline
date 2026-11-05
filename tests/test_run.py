import gzip
import json
from datetime import date, datetime, timezone

import pytest

from nsw_pipeline import client, report, storage
from nsw_pipeline.client import FetchError
from nsw_pipeline.run import request_window, run
from tests.conftest import SAMPLE_FETCHED_AT, load

UTC = timezone.utc


def fake_sources(sites=None, observations=None, fail_with=None, failures=None):
    """Stand-ins for the API, backed by the saved samples."""
    sites = load("sites.json") if sites is None else sites
    observations = load("observations_current.json") if observations is None else observations
    requests = []

    def fetch_sites():
        return sites, json.dumps(sites)

    def fetch_observations(site_ids, start, end_exclusive, sites_by_parameter=None):
        requests.append((site_ids, start, end_exclusive, sites_by_parameter))
        if fail_with:
            raise fail_with
        return observations, json.dumps(observations), list(failures or [])

    return fetch_sites, fetch_observations, requests


@pytest.fixture(autouse=True)
def fake_latest(monkeypatch):
    """Never call the real API: the "latest hour" lookup returns the saved sample."""
    observations = load("observations_current.json")
    monkeypatch.setattr(client, "fetch_latest", lambda **kw: (observations, json.dumps(observations)))


def test_window_covers_lookback_in_aest():
    # 22:08 UTC on 31 Oct is 08:08 AEST on 1 Nov; 24 hours earlier is 31 Oct AEST
    start, end_exclusive = request_window(SAMPLE_FETCHED_AT, lookback_hours=24)
    assert (start, end_exclusive) == (date(2026, 10, 31), date(2026, 11, 2))


def test_successful_run(tmp_path):
    fetch_sites, fetch_obs, requests = fake_sources()
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)

    assert summary["status"] == "success"
    assert summary["records_fetched"] == 1195
    assert summary["accepted"] == 241 and summary["new"] == 241
    # The sample's 31 blank readings are counted, not logged as rejects
    assert (summary["rejected"], summary["blank"]) == (0, 31)
    assert not (tmp_path / "data" / "rejected").exists()
    # Only sites that reported one of our pollutants in the latest hour
    assert summary["sites_requested"] == 59

    site_ids, start, end, by_param = requests[0]
    assert 805 not in site_ids and 10000000 not in site_ids
    assert set(by_param) == {"CO", "NO2", "OZONE", "PM10", "PM2.5", "SO2"}
    assert len(by_param["PM10"]) == 59 and len(by_param["CO"]) == 37

    raw_files = sorted(p.name for p in (tmp_path / "raw").iterdir())
    assert raw_files == ["20261031T220815Z_observations.json.gz", "20261031T220815Z_sites.json.gz"]
    with gzip.open(tmp_path / "raw" / raw_files[0], "rt", encoding="utf-8") as f:
        assert len(json.load(f)) == 1195

    runs = storage.read_runs(tmp_path / "data")
    assert [r["status"] for r in runs] == ["success"]


def test_repeated_runs_do_not_duplicate(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources()
    kwargs = dict(data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    run(now=SAMPLE_FETCHED_AT, **kwargs)
    second = run(now=datetime(2026, 10, 31, 23, 8, tzinfo=UTC), **kwargs)

    assert (second["new"], second["unchanged"]) == (0, 241)
    assert len(storage.read_measurements(tmp_path / "data")) == 241
    assert len(storage.read_runs(tmp_path / "data")) == 2


def test_failed_fetch_is_logged_and_reported(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources(fail_with=FetchError("get_Observations failed after 4 attempts"))
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)

    assert summary["status"] == "failed"
    assert "failed after 4 attempts" in summary["error"]
    runs = storage.read_runs(tmp_path / "data")
    assert runs[0]["status"] == "failed"
    assert not (tmp_path / "data" / "measurements").exists()


def test_empty_response_is_flagged(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources(observations=[])
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    assert summary["status"] == "no_data"


def test_report_after_runs(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources()
    data_dir = tmp_path / "data"
    run(now=SAMPLE_FETCHED_AT, data_dir=data_dir, raw_dir=tmp_path / "raw",
        fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    broken_sites, broken_obs, _ = fake_sources(fail_with=FetchError("timeout"))
    run(now=datetime(2026, 10, 31, 23, 8, tzinfo=UTC), data_dir=data_dir, raw_dir=tmp_path / "raw",
        fetch_sites=broken_sites, fetch_observations=broken_obs)

    text = report.build_report(data_dir)
    assert "Total runs:     2" in text
    assert "Succeeded:      1 (50%)" in text
    assert "timeout" in text
    assert "Measurements:   241" in text
    assert "Completeness:   100.0%" in text
    assert "REJECTED RECORDS\n  None" in text


def test_report_with_no_runs(tmp_path):
    assert "No runs recorded yet" in report.build_report(tmp_path)


def test_partial_run_stores_data_and_is_flagged(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources(failures=["SO2, sites 15-99 (30): timed out"])
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    assert summary["status"] == "partial"
    assert summary["failed_requests"] == 1
    assert "timed out" in summary["error"]
    assert len(storage.read_measurements(tmp_path / "data")) == 241


def test_old_run_log_columns_are_upgraded(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "runs.csv").write_text(
        "run_at,status,error\n2026-11-04T22:39:26Z,failed,TimeoutError\n", encoding="utf-8")
    fetch_sites, fetch_obs, _ = fake_sources()
    run(now=SAMPLE_FETCHED_AT, data_dir=data_dir, raw_dir=tmp_path / "raw",
        fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    runs = storage.read_runs(data_dir)
    assert [(r["status"], r["error"]) for r in runs] == [("failed", "TimeoutError"), ("success", "")]
    assert runs[1]["failed_requests"] == "0"


def test_falls_back_to_all_sites_if_lookup_fails(tmp_path, monkeypatch):
    def broken(**kw):
        raise FetchError("timeout")
    monkeypatch.setattr(client, "fetch_latest", broken)
    fetch_sites, fetch_obs, requests = fake_sources()
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    assert summary["status"] == "success"
    assert summary["sites_requested"] == 121
    assert requests[0][3] is None


def test_ctrl_c_is_logged_as_interrupted(tmp_path):
    fetch_sites, fetch_obs, _ = fake_sources(fail_with=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
            fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    runs = storage.read_runs(tmp_path / "data")
    assert (runs[0]["status"], runs[0]["error"]) == ("interrupted", "Stopped by user (Ctrl+C)")


def test_blanks_never_erase_stored_values(tmp_path):
    # The API sometimes returns a blank for a reading it returned earlier.
    data_dir = tmp_path / "data"
    observations = load("observations_current.json")
    fetch_sites, fetch_obs, _ = fake_sources(observations=observations)
    run(now=SAMPLE_FETCHED_AT, data_dir=data_dir, raw_dir=tmp_path / "raw",
        fetch_sites=fetch_sites, fetch_observations=fetch_obs)

    blanked = [dict(o, Value=None) for o in observations]
    fetch_sites, fetch_obs, _ = fake_sources(observations=blanked)
    second = run(now=datetime(2026, 10, 31, 23, 8, tzinfo=UTC), data_dir=data_dir,
                 raw_dir=tmp_path / "raw", fetch_sites=fetch_sites, fetch_observations=fetch_obs)

    assert second["status"] == "no_data"
    assert second["blank"] == 272
    assert len(storage.read_measurements(data_dir)) == 241


def test_invalid_values_still_go_to_rejected_log(tmp_path):
    observations = load("observations_current.json")
    for o in observations:
        if o["Parameter"]["ParameterCode"] == "PM2.5" and o["Parameter"]["Frequency"] == "Hourly average" \
                and o["Value"] is not None:
            o["Value"] = 9999.0
            break
    fetch_sites, fetch_obs, _ = fake_sources(observations=observations)
    summary = run(now=SAMPLE_FETCHED_AT, data_dir=tmp_path / "data", raw_dir=tmp_path / "raw",
                  fetch_sites=fetch_sites, fetch_observations=fetch_obs)
    assert summary["rejected"] == 1
    rows = list((tmp_path / "data" / "rejected").glob("*.csv"))
    assert len(rows) == 1 and "out_of_range" in rows[0].read_text(encoding="utf-8")
