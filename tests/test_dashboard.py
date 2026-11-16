"""Tests for the dashboard's data layer (dashboard/data.py). No network needed."""
import sys
from datetime import date
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
import data  # noqa: E402

from nsw_pipeline import normalize, storage  # noqa: E402
from tests.conftest import load  # noqa: E402


def rows(*specs):
    """Measurement rows: (location_id, name, parameter, value, datetime_to)."""
    return pd.DataFrame([
        {"location_id": lid, "location_name": name, "latitude": -33.9, "longitude": 151.1,
         "parameter": p, "value": v, "unit": "µg/m³",
         "datetime_from": "", "datetime_to": t}
        for lid, name, p, v, t in specs
    ])


@pytest.fixture
def locations():
    locs = normalize.build_locations(load("sites.json"))
    return pd.DataFrame(normalize.location_rows(locs))


def test_hour_is_labelled_by_its_start_in_sydney_time():
    df = data.prepare_measurements(rows(("nsw-39", "ROZELLE", "pm25", 7.0, "2026-11-04T23:00:00Z")))
    # 22:00-23:00 UTC = 9-10 am AEDT on 5 Nov (Sydney is on daylight saving in November)
    assert df["hour_local"].iloc[0] == pd.Timestamp("2026-11-05 09:00", tz="Australia/Sydney")


def test_greater_sydney_uses_sydney_regions(locations):
    ids = data.station_ids_in(data.SYDNEY, locations)
    assert "nsw-39" in ids          # Rozelle, Sydney East
    assert "nsw-107" in ids         # Liverpool, Sydney South-west
    assert "nsw-500" not in ids     # Wollongong, Illawarra
    assert "nsw-1107" not in ids    # Liverpool SWAQS, "Research Monitoring"


def test_region_options_start_with_sydney(locations):
    opts = data.region_options(locations)
    assert opts[:2] == [data.SYDNEY, data.ALL_NSW]
    assert "Illawarra" in opts and "Sydney East" in opts


def test_sydney_fallback_without_station_list():
    df = data.prepare_measurements(rows(
        ("nsw-39", "ROZELLE", "pm25", 7.0, "2026-11-04T23:00:00Z")))
    df.loc[0, ["latitude", "longitude"]] = [-33.86, 151.16]
    locs = data.locations_from_measurements(df)
    assert data.station_ids_in(data.SYDNEY, locs) == {"nsw-39"}


def test_hourly_average_and_station_count():
    df = data.prepare_measurements(rows(
        ("a", "A", "pm25", 10.0, "2026-11-04T01:00:00Z"),
        ("b", "B", "pm25", 20.0, "2026-11-04T01:00:00Z"),
        ("a", "A", "pm25", 30.0, "2026-11-04T02:00:00Z"),
    ))
    out = data.hourly_average(df)
    assert list(out["mean"]) == [15.0, 30.0]
    assert list(out["stations"]) == [2, 1]
    assert data.latest_snapshot(df)[1:] == (30.0, 1)


def test_daily_means_need_18_hours():
    # Station a: 18 hours on one Sydney day; station b: 17 hours
    specs = [("a", "A", "pm25", 10.0, f"2026-11-03T{h:02d}:00:00Z") for h in range(14, 24)]
    specs += [("a", "A", "pm25", 10.0, f"2026-11-04T{h:02d}:00:00Z") for h in range(0, 8)]
    specs += [("b", "B", "pm25", 50.0, f"2026-11-03T{h:02d}:00:00Z") for h in range(14, 24)]
    specs += [("b", "B", "pm25", 50.0, f"2026-11-04T{h:02d}:00:00Z") for h in range(0, 7)]
    out = data.daily_station_means(data.prepare_measurements(rows(*specs)))
    assert list(out["location_id"]) == ["a"]
    assert out["hours"].iloc[0] == 18


def test_last_24h_by_station_ranks_highest_first(locations):
    df = data.prepare_measurements(rows(
        ("nsw-39", "ROZELLE", "pm25", 5.0, "2026-11-04T10:00:00Z"),
        ("nsw-107", "LIVERPOOL", "pm25", 25.0, "2026-11-04T10:00:00Z"),
        ("nsw-107", "LIVERPOOL", "pm25", 99.0, "2026-11-03T09:00:00Z"),  # older than 24 h
    ))
    out = data.last_24h_by_station(df, locations)
    assert list(out["location_id"]) == ["nsw-107", "nsw-39"]
    assert list(out["mean"]) == [25.0, 5.0]
    assert out["latitude"].notna().all()


def test_loads_local_files_and_skips_missing_days(tmp_path):
    accepted, _, _ = normalize.normalize(
        load("observations_current.json"), load("sites.json"),
        pd.Timestamp("2026-10-31T22:08:15Z").to_pydatetime())
    storage.upsert_measurements(accepted, tmp_path)
    storage.append_run({"run_at": "2026-10-31T22:08:15Z", "status": "success"}, tmp_path)

    df = data.load_measurements(3, source=str(tmp_path), today=date(2026, 11, 2))
    assert len(df) == 241
    assert data.read_csv("locations.csv", source=str(tmp_path)) is None

    health = data.pipeline_health(data.read_csv("runs.csv", source=str(tmp_path)), df)
    assert (health["runs"], health["succeeded"], health["completeness"]) == (1, 1, 1.0)


def test_no_data_gives_empty_frames():
    df = data.load_measurements(2, source="/nonexistent-folder", today=date(2026, 11, 2))
    assert df.empty
    assert data.latest_snapshot(df) is None
    assert data.hourly_average(df).empty


@pytest.mark.parametrize("raw,expected", [
    ("COOK AND PHILLIP", "Cook and Phillip"),
    ("ULTIMO-UTS", "Ultimo-UTS"),
    ("MUSWELLBROOK NW", "Muswellbrook NW"),
    ("Armidale ", "Armidale"),
    ("PARRAMATTA NORTH", "Parramatta North"),
])
def test_display_names(raw, expected):
    assert data.display_name(raw) == expected
