from datetime import datetime, timezone

import pytest

from nsw_pipeline import normalize
from nsw_pipeline.normalize import normalize as run_normalize, period_utc
from tests.conftest import SAMPLE_FETCHED_AT, make_record

UTC = timezone.utc


# Timestamps

def test_hour_label_is_hour_ending_in_aest():
    # Hour 7 on 1 Nov = 6-7 am AEST = 20:00-21:00 UTC on 31 Oct
    start, end = period_utc("2026-11-01", 7)
    assert start == datetime(2026, 10, 31, 20, tzinfo=UTC)
    assert end == datetime(2026, 10, 31, 21, tzinfo=UTC)


def test_hour_24_ends_at_midnight():
    start, end = period_utc("2026-10-03", 24)
    assert end == datetime(2026, 10, 3, 14, tzinfo=UTC)  # midnight AEST = 14:00 UTC


def test_no_daylight_saving_on_changeover_day():
    # NSW clocks skipped 2-3 am on 4 Oct 2026, but the data is AEST, so every
    # hour is exactly one hour after the last.
    ends = [period_utc("2026-10-04", h)[1] for h in range(1, 25)]
    assert all((b - a).total_seconds() == 3600 for a, b in zip(ends, ends[1:]))


@pytest.mark.parametrize("hour", [0, 25, None, "7", 7.5, True])
def test_invalid_hours_raise(hour):
    with pytest.raises(ValueError):
        period_utc("2026-11-01", hour)


# Units

@pytest.mark.parametrize("code,units,value,expected_param,expected_value,expected_unit", [
    ("PM2.5", "µg/m³", 7.2, "pm25", 7.2, "µg/m³"),
    ("PM10", "µg/m³", 20.0, "pm10", 20.0, "µg/m³"),
    ("NO2", "pphm", 1.5, "no2", 0.015, "ppm"),
    ("OZONE", "pphm", 2.9, "o3", 0.029, "ppm"),
    ("SO2", "pphm", 0.4, "so2", 0.004, "ppm"),
    ("CO", "ppm", 0.3, "co", 0.3, "ppm"),
])
def test_unit_conversion(sites, code, units, value, expected_param, expected_value, expected_unit):
    accepted, rejected, _ = run_normalize(
        [make_record(code=code, units=units, value=value)], sites, SAMPLE_FETCHED_AT)
    assert rejected == []
    m = accepted[0]
    assert (m["parameter"], m["unit"]) == (expected_param, expected_unit)
    assert m["value"] == pytest.approx(expected_value)
    assert m["source_value"] == value


def test_location_fields(sites):
    accepted, _, _ = run_normalize([make_record(site_id=39)], sites, SAMPLE_FETCHED_AT)
    m = accepted[0]
    assert m["location_id"] == "nsw-39"
    assert m["location_name"] == "ROZELLE"
    assert m["latitude"] == pytest.approx(-33.86433)
    assert m["longitude"] == pytest.approx(151.16395)


def test_site_names_are_trimmed(sites):
    accepted, _, _ = run_normalize([make_record(site_id=9350)], sites, SAMPLE_FETCHED_AT)
    assert accepted[0]["location_name"] == "Armidale"  # source has "Armidale "


# Out-of-scope records are skipped, not rejected

@pytest.mark.parametrize("overrides", [
    {"code": "TEMP", "units": "°C"},
    {"code": "AQC", "units": "category", "category": "Site AQC"},
    {"code": "NEPH", "units": "10^-4 m^-1"},
    {"code": "PM2.5d"},
    {"code": "NO", "units": "pphm"},
    {"frequency": "24h rolling average derived from 1h average"},
    {"category": "Maximums"},
])
def test_out_of_scope_records_are_skipped(sites, overrides):
    accepted, rejected, skipped = run_normalize([make_record(**overrides)], sites, SAMPLE_FETCHED_AT)
    assert (accepted, rejected, skipped) == ([], [], 1)


# Validation

@pytest.mark.parametrize("overrides,reason", [
    ({"value": None}, "missing_value"),
    ({"value": "7.2"}, "invalid_value"),
    ({"site_id": 805}, "excluded_site"),
    ({"site_id": 123456}, "unknown_site"),
    ({"site_id": 10000000}, "missing_coordinates"),
    ({"units": "ppb"}, "unexpected_unit"),
    ({"hour": 0}, "invalid_timestamp"),
    ({"date": "not-a-date"}, "invalid_timestamp"),
    ({"value": -10.5}, "out_of_range"),
    ({"value": 2500.0}, "out_of_range"),
    ({"code": "NO2", "units": "pphm", "value": -2.0}, "out_of_range"),
])
def test_invalid_records_are_rejected_with_reason(sites, overrides, reason):
    accepted, rejected, _ = run_normalize([make_record(**overrides)], sites, SAMPLE_FETCHED_AT)
    assert accepted == []
    assert rejected[0]["reason"] == reason


def test_small_negative_values_are_kept_as_reported(sites):
    # Reference monitors report small negatives near zero; OpenAQ wants raw values.
    accepted, rejected, _ = run_normalize(
        [make_record(value=-5.838), make_record(code="CO", units="ppm", value=-0.143)],
        sites, SAMPLE_FETCHED_AT)
    assert rejected == []
    assert [m["value"] for m in accepted] == [-5.838, -0.143]


def test_reject_record_keeps_source_details(sites):
    _, rejected, _ = run_normalize([make_record(site_id=39, value=None)], sites, SAMPLE_FETCHED_AT)
    r = rejected[0]
    assert (r["source_site_id"], r["source_parameter"], r["date"], r["hour"]) == (39, "PM2.5", "2026-11-01", 7)
    assert r["fetched_at"] == "2026-10-31T22:08:15Z"


# The real saved sample

def test_real_sample_every_record_is_accounted_for(sites, observations):
    accepted, rejected, skipped = run_normalize(observations, sites, SAMPLE_FETCHED_AT)
    assert len(accepted) + len(rejected) + skipped == len(observations) == 1195


def test_real_sample_counts(sites, observations):
    accepted, rejected, _ = run_normalize(observations, sites, SAMPLE_FETCHED_AT)
    by_param = {}
    for m in accepted:
        by_param[m["parameter"]] = by_param.get(m["parameter"], 0) + 1
    # Hourly-average rows in the sample minus null values (see SOURCE_NOTES.md)
    assert by_param == {"pm25": 47, "pm10": 57, "no2": 42, "o3": 41, "so2": 30, "co": 24}
    assert {r["reason"] for r in rejected} == {"missing_value"}
    assert len(rejected) == 31


def test_real_sample_all_in_one_hour(sites, observations):
    accepted, _, _ = run_normalize(observations, sites, SAMPLE_FETCHED_AT)
    assert {(m["datetime_from"], m["datetime_to"]) for m in accepted} == {
        ("2026-10-31T20:00:00Z", "2026-10-31T21:00:00Z")}


def test_real_sample_values_within_ranges(sites, observations):
    accepted, _, _ = run_normalize(observations, sites, SAMPLE_FETCHED_AT)
    for m in accepted:
        low, high = normalize.VALID_RANGES[m["parameter"]]
        assert low <= m["value"] <= high


def test_collectable_sites_exclude_test_and_regional_sites(sites):
    ids = normalize.collectable_site_ids(normalize.build_locations(sites))
    assert 805 not in ids
    assert 10000000 not in ids
    assert 39 in ids
    assert len(ids) == 139 - 17 - 1  # 17 regional groupings without coordinates, 1 test site


# Hours that haven't ended yet

def test_blank_future_hour_is_skipped_not_rejected(sites):
    # Hour 9 on 1 Nov AEST ends at 23:00 UTC on 31 Oct, after the 22:08 fetch
    accepted, rejected, skipped = run_normalize(
        [make_record(hour=9, value=None)], sites, SAMPLE_FETCHED_AT)
    assert (accepted, rejected, skipped) == ([], [], 1)


def test_blank_past_hour_is_still_rejected(sites):
    _, rejected, _ = run_normalize([make_record(hour=7, value=None)], sites, SAMPLE_FETCHED_AT)
    assert rejected[0]["reason"] == "missing_value"


def test_sites_by_parameter_from_latest_hour(sites, observations):
    allowed = normalize.collectable_site_ids(normalize.build_locations(sites))
    by_param = normalize.sites_by_parameter(observations, allowed)
    assert {code: len(ids) for code, ids in by_param.items()} == {
        "CO": 37, "NO2": 45, "OZONE": 45, "PM10": 59, "PM2.5": 49, "SO2": 37}
    assert 805 not in set().union(*by_param.values())
