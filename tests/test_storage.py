import csv

from nsw_pipeline import storage
from nsw_pipeline.normalize import normalize
from tests.conftest import SAMPLE_FETCHED_AT, make_record


def _measurements(sites, *records):
    accepted, _, _ = normalize(list(records), sites, SAMPLE_FETCHED_AT)
    return accepted


def _rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_first_write_counts_everything_as_new(tmp_path, sites, observations):
    accepted, _, _ = normalize(observations, sites, SAMPLE_FETCHED_AT)
    counts = storage.upsert_measurements(accepted, tmp_path)
    assert counts == {"new": len(accepted), "updated": 0, "unchanged": 0}
    assert len(_rows(tmp_path / "measurements" / "2026-10-31.csv")) == len(accepted)


def test_same_data_twice_is_stored_once(tmp_path, sites, observations):
    accepted, _, _ = normalize(observations, sites, SAMPLE_FETCHED_AT)
    storage.upsert_measurements(accepted, tmp_path)
    counts = storage.upsert_measurements(accepted, tmp_path)
    assert counts == {"new": 0, "updated": 0, "unchanged": len(accepted)}
    assert len(_rows(tmp_path / "measurements" / "2026-10-31.csv")) == len(accepted)


def test_revised_value_replaces_stored_row(tmp_path, sites):
    storage.upsert_measurements(_measurements(sites, make_record(value=7.2)), tmp_path)
    counts = storage.upsert_measurements(_measurements(sites, make_record(value=8.0)), tmp_path)
    assert counts == {"new": 0, "updated": 1, "unchanged": 0}
    rows = _rows(tmp_path / "measurements" / "2026-10-31.csv")
    assert [r["value"] for r in rows] == ["8.0"]


def test_files_are_split_by_utc_day(tmp_path, sites):
    # Hour 7 on 1 Nov AEST is still 31 Oct in UTC; Hour 24 on 1 Nov is 1 Nov UTC
    storage.upsert_measurements(
        _measurements(sites, make_record(hour=7), make_record(hour=24)), tmp_path)
    files = sorted(p.name for p in (tmp_path / "measurements").iterdir())
    assert files == ["2026-10-31.csv", "2026-11-01.csv"]


def test_rejects_are_logged_once(tmp_path, sites):
    _, rejected, _ = normalize([make_record(value=None)], sites, SAMPLE_FETCHED_AT)
    storage.upsert_rejected(rejected, tmp_path)
    storage.upsert_rejected(rejected, tmp_path)
    rows = _rows(tmp_path / "rejected" / "2026-11-01.csv")
    assert len(rows) == 1
    assert rows[0]["reason"] == "missing_value"


def test_run_log_appends(tmp_path):
    storage.append_run({"run_at": "a", "status": "success"}, tmp_path)
    storage.append_run({"run_at": "b", "status": "failed", "error": "boom"}, tmp_path)
    runs = storage.read_runs(tmp_path)
    assert [(r["run_at"], r["status"], r["error"]) for r in runs] == [
        ("a", "success", ""), ("b", "failed", "boom")]


def test_no_temp_files_left_behind(tmp_path, sites):
    storage.upsert_measurements(_measurements(sites, make_record()), tmp_path)
    assert not list(tmp_path.rglob("*.tmp"))


def test_completeness_counts_gaps_inside_each_series():
    from nsw_pipeline.report import completeness
    rows = [
        {"location_id": "nsw-39", "parameter": "pm25", "datetime_to": "2026-11-04T01:00:00Z"},
        {"location_id": "nsw-39", "parameter": "pm25", "datetime_to": "2026-11-04T04:00:00Z"},  # 2 hours missing
        {"location_id": "nsw-15", "parameter": "co", "datetime_to": "2026-11-04T02:00:00Z"},
    ]
    assert completeness(rows) == (3, 5)


def test_files_use_unix_line_endings(tmp_path, sites):
    # Same bytes on Windows and Linux, so Git diffs show only real changes
    storage.upsert_measurements(_measurements(sites, make_record()), tmp_path)
    storage.append_run({"run_at": "a", "status": "success"}, tmp_path)
    for path in [tmp_path / "measurements" / "2026-10-31.csv", tmp_path / "runs.csv"]:
        assert b"\r" not in path.read_bytes()


def test_reads_files_with_windows_line_endings(tmp_path, sites):
    # Files written by the earlier version still merge correctly
    rows = _measurements(sites, make_record())
    storage.upsert_measurements(rows, tmp_path)
    path = tmp_path / "measurements" / "2026-10-31.csv"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert storage.upsert_measurements(rows, tmp_path) == {"new": 0, "updated": 0, "unchanged": 1}
    assert b"\r" not in path.read_bytes()
