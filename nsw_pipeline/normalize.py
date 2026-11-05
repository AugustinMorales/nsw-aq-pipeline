"""
Convert raw NSW API records into OpenAQ-style measurements.

Every observation record ends up in exactly one of three groups:

- accepted: a valid pollutant measurement, converted to OpenAQ units and UTC
- rejected: in scope (a pollutant we collect) but failed validation; each
  rejected record carries a reason so it can be logged and reviewed
- skipped: out of scope by design (weather, air-quality categories, rolling
  averages, DustWatch, NO, NH3), or an hour that hadn't ended when fetched;
  counted but not logged individually

See SOURCE_NOTES.md for where these rules come from.
"""
from datetime import date, datetime, timedelta, timezone

# NSW timestamps are Australian Eastern Standard Time all year (no daylight
# saving), confirmed with check_timezone.py.
AEST = timezone(timedelta(hours=10))

# NSW parameter code -> (OpenAQ parameter, NSW unit, OpenAQ unit, multiplier)
# pphm (parts per hundred million) / 100 = ppm.
PARAMETERS = {
    "PM2.5": ("pm25", "µg/m³", "µg/m³", 1.0),
    "PM10": ("pm10", "µg/m³", "µg/m³", 1.0),
    "NO2": ("no2", "pphm", "ppm", 0.01),
    "OZONE": ("o3", "pphm", "ppm", 0.01),
    "SO2": ("so2", "pphm", "ppm", 0.01),
    "CO": ("co", "ppm", "ppm", 1.0),
}

# Only plain hourly averages are measurements; other categories and
# frequencies are derived values (rolling averages, maximums, exceedances).
CATEGORY = "Averages"
SUB_CATEGORY = "Hourly"
FREQUENCY = "Hourly average"

# Accepted range for each pollutant, in OpenAQ units, inclusive.
# Lower bounds allow the small negative values that reference monitors
# report near zero (instrument noise), while rejecting implausible ones.
# Upper bounds are generous so real extreme events (such as bushfire smoke)
# are kept, while obvious instrument faults are rejected.
VALID_RANGES = {
    "pm25": (-10.0, 2000.0),
    "pm10": (-10.0, 3000.0),
    "no2": (-0.01, 1.0),
    "o3": (-0.01, 0.5),
    "so2": (-0.01, 2.0),
    "co": (-0.5, 50.0),
}

# Sites to ignore even if they report data.
EXCLUDED_SITE_IDS = {805}  # "Test Site"

MEASUREMENT_FIELDS = [
    "location_id", "location_name", "latitude", "longitude",
    "parameter", "value", "unit",
    "datetime_from", "datetime_to",
    "source_site_id", "source_parameter", "source_value", "source_unit",
    "fetched_at",
]

REJECT_FIELDS = [
    "reason", "detail", "source_site_id", "source_parameter",
    "date", "hour", "source_value", "fetched_at",
]


def period_utc(date_str, hour):
    """Return (start, end) of the hour in UTC.

    NSW labels each hour by when it *ends*: Hour 7 is "6 am - 7 am" and
    Hour 24 is "11 pm - midnight", in AEST.
    """
    if isinstance(hour, bool) or not isinstance(hour, int) or not 1 <= hour <= 24:
        raise ValueError(f"hour must be an integer from 1 to 24, got {hour!r}")
    day = date.fromisoformat(date_str)
    end_local = datetime(day.year, day.month, day.day, tzinfo=AEST) + timedelta(hours=hour)
    end = end_local.astimezone(timezone.utc)
    return end - timedelta(hours=1), end


def iso_utc(dt):
    """Format a UTC datetime as e.g. 2026-10-31T21:00:00Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_locations(sites):
    """Map Site_Id -> site details, trimming names."""
    locations = {}
    for site in sites:
        site_id = site.get("Site_Id")
        if site_id is None:
            continue
        locations[site_id] = {
            "name": (site.get("SiteName") or "").strip(),
            "latitude": site.get("Latitude"),
            "longitude": site.get("Longitude"),
        }
    return locations


def collectable_site_ids(locations):
    """Sites worth requesting: real coordinates and not excluded."""
    return sorted(
        site_id for site_id, loc in locations.items()
        if site_id not in EXCLUDED_SITE_IDS
        and loc["latitude"] is not None and loc["longitude"] is not None
    )


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def in_scope(record):
    """True if the record is a pollutant hourly average we collect."""
    p = record.get("Parameter") or {}
    return (
        p.get("ParameterCode") in PARAMETERS
        and p.get("Category") == CATEGORY
        and p.get("SubCategory") == SUB_CATEGORY
        and p.get("Frequency") == FREQUENCY
    )


def sites_by_parameter(latest_records, allowed_site_ids):
    """Which allowed sites report each pollutant we collect.

    Built from the API's "latest hour" response, which lists every site and
    parameter currently configured (including ones with a missing value), so
    requests can skip sites that never measure a given pollutant.
    """
    allowed = set(allowed_site_ids)
    found = {code: set() for code in PARAMETERS}
    for record in latest_records:
        if isinstance(record, dict) and in_scope(record) and record.get("Site_Id") in allowed:
            found[record["Parameter"]["ParameterCode"]].add(record["Site_Id"])
    return {code: sorted(ids) for code, ids in found.items() if ids}


def normalize_record(record, locations, fetched_at):
    """Return (measurement, None) if valid, (None, reject) if invalid,
    or (None, None) for an hour that hasn't happened yet.

    The API only takes whole dates, so a request for today also returns
    today's future hours, with no value. Those are skipped, not rejected.
    """
    p = record.get("Parameter") or {}
    code = p.get("ParameterCode")
    site_id = record.get("Site_Id")
    raw_value = record.get("Value")
    fetched = iso_utc(fetched_at)

    def reject(reason, detail=""):
        return None, {
            "reason": reason, "detail": detail,
            "source_site_id": site_id, "source_parameter": code,
            "date": record.get("Date"), "hour": record.get("Hour"),
            "source_value": raw_value, "fetched_at": fetched,
        }

    parameter, source_unit, unit, multiplier = PARAMETERS[code]

    if site_id in EXCLUDED_SITE_IDS:
        return reject("excluded_site")
    location = locations.get(site_id)
    if location is None:
        return reject("unknown_site", "Site_Id not in get_SiteDetails")
    lat, lon = location["latitude"], location["longitude"]
    if not (_is_number(lat) and _is_number(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return reject("missing_coordinates", f"lat={lat}, lon={lon}")
    if p.get("Units") != source_unit:
        return reject("unexpected_unit", f"expected {source_unit}, got {p.get('Units')}")
    try:
        start, end = period_utc(record.get("Date"), record.get("Hour"))
    except (TypeError, ValueError) as err:
        return reject("invalid_timestamp", str(err))
    if raw_value is None:
        if end > fetched_at:
            return None, None  # hour not over yet when fetched: nothing to report
        return reject("missing_value")
    if not _is_number(raw_value):
        return reject("invalid_value", f"not a number: {raw_value!r}")

    value = round(raw_value * multiplier, 8)
    low, high = VALID_RANGES[parameter]
    if not low <= value <= high:
        return reject("out_of_range", f"{value} {unit} outside [{low}, {high}]")

    return {
        "location_id": f"nsw-{site_id}",
        "location_name": location["name"],
        "latitude": lat,
        "longitude": lon,
        "parameter": parameter,
        "value": value,
        "unit": unit,
        "datetime_from": iso_utc(start),
        "datetime_to": iso_utc(end),
        "source_site_id": site_id,
        "source_parameter": code,
        "source_value": raw_value,
        "source_unit": source_unit,
        "fetched_at": fetched,
    }, None


def normalize(observations, sites, fetched_at):
    """Split raw observations into (accepted, rejected, skipped_count)."""
    locations = build_locations(sites)
    accepted, rejected, skipped = [], [], 0
    for record in observations:
        if not isinstance(record, dict) or not in_scope(record):
            skipped += 1
            continue
        measurement, reject = normalize_record(record, locations, fetched_at)
        if measurement is not None:
            accepted.append(measurement)
        elif reject is not None:
            rejected.append(reject)
        else:
            skipped += 1
    return accepted, rejected, skipped
