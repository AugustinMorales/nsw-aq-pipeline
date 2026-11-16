"""
Data loading and calculations for the dashboard.

The dashboard reads the CSV files the pipeline commits. By default it reads
the local data/ folder; set DATA_BASE_URL to read them straight from GitHub
instead (the deployed dashboard does this, so it shows new data as soon as
the pipeline commits it, without redeploying).

Everything except the loaders is a plain pandas function, so it can be
tested without Streamlit or the network.
"""
import io
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from nsw_pipeline.report import completeness  # noqa: E402  (reuse the pipeline's own measure)

LOCAL_TZ = "Australia/Sydney"
SYDNEY = "Greater Sydney"
ALL_NSW = "All stations"

POLLUTANTS = {  # parameter -> (label, unit)
    "pm25": ("PM2.5", "µg/m³"),
    "pm10": ("PM10", "µg/m³"),
    "no2": ("NO₂", "ppm"),
    "o3": ("O₃", "ppm"),
    "so2": ("SO₂", "ppm"),
    "co": ("CO", "ppm"),
}

# WHO 2021 air quality guidelines, 24-hour mean. Only shown for the
# particle pollutants, which NSW reports in the same unit as the guideline.
WHO_24H = {"pm25": 15.0, "pm10": 45.0}

# Used only if data/locations.csv is missing (older pipeline versions):
# a box around the Sydney basin, roughly matching the "Sydney ..." regions.
SYDNEY_BOX = {"lat": (-34.15, -33.55), "lon": (150.60, 151.35)}

# A day's average only counts if at least 75% of its hours have data,
# a common rule for daily air-quality averages.
MIN_HOURS_PER_DAY = 18


def data_source():
    """The base URL or folder the CSVs are read from."""
    return os.environ.get("DATA_BASE_URL", "").rstrip("/") or str(REPO_ROOT / "data")


def read_csv(name, source=None, timeout_s=30):
    """Read one CSV (e.g. "runs.csv") from the source. Returns None if it doesn't exist."""
    source = source or data_source()
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(f"{source}/{name}", timeout=timeout_s) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise
        return pd.read_csv(io.StringIO(text))
    path = Path(source) / name
    return pd.read_csv(path) if path.exists() else None


def load_measurements(days, source=None, today=None):
    """Measurements for the last `days` UTC days (one file per day)."""
    today = today or datetime.now(timezone.utc).date()
    names = [f"measurements/{today - timedelta(days=i)}.csv" for i in range(days + 1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = [f for f in pool.map(lambda n: read_csv(n, source), names) if f is not None]
    if not frames:
        return empty_measurements()
    return prepare_measurements(pd.concat(frames, ignore_index=True))


def empty_measurements():
    return prepare_measurements(pd.DataFrame(columns=[
        "location_id", "location_name", "latitude", "longitude",
        "parameter", "value", "unit", "datetime_from", "datetime_to"]))


def prepare_measurements(df):
    """Parse times and numbers; add the hour in Sydney local time."""
    df = df.copy()
    df["datetime_to"] = pd.to_datetime(df["datetime_to"], utc=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["value"])
    # The value covers the hour *ending* at datetime_to; label it by its start
    df["hour_local"] = (df["datetime_to"] - pd.Timedelta(hours=1)).dt.tz_convert(LOCAL_TZ)
    return df


# Words that title case gets wrong in NSW station names
_NAME_FIXES = {"And": "and", "Uts": "UTS", "Nw": "NW", "Swaqs": "SWAQS", "Rr": "RR", "Pod2": "POD2"}


def display_name(name):
    """'COOK AND PHILLIP' -> 'Cook and Phillip', 'ULTIMO-UTS' -> 'Ultimo-UTS'."""
    words = str(name).strip().title().replace("-", " - ").split(" ")
    return " ".join(_NAME_FIXES.get(w, w) for w in words).replace(" - ", "-")


def region_options(locations):
    """Choices for the region filter: Greater Sydney first, then each NSW region."""
    regions = sorted(r for r in locations["region"].dropna().unique() if r)
    return [SYDNEY, ALL_NSW] + regions


def locations_from_measurements(measurements):
    """Fallback station list when data/locations.csv doesn't exist yet (no regions)."""
    cols = ["location_id", "location_name", "latitude", "longitude"]
    out = measurements[cols].drop_duplicates("location_id").copy()
    out["region"] = ""
    return out


def station_ids_in(region, locations):
    """Location IDs for a region choice. Greater Sydney = the NSW regions named 'Sydney ...'."""
    if region == ALL_NSW:
        return set(locations["location_id"])
    if region == SYDNEY and not locations["region"].fillna("").str.len().any():
        lat, lon = SYDNEY_BOX["lat"], SYDNEY_BOX["lon"]
        mask = locations["latitude"].between(*lat) & locations["longitude"].between(*lon)
    elif region == SYDNEY:
        mask = locations["region"].fillna("").str.startswith("Sydney")
    else:
        mask = locations["region"] == region
    return set(locations.loc[mask, "location_id"])


def select(measurements, parameter, station_ids, since=None):
    """Rows for one pollutant at the given stations, optionally from a UTC time onward."""
    df = measurements[(measurements["parameter"] == parameter)
                      & (measurements["location_id"].isin(station_ids))]
    if since is not None:
        df = df[df["datetime_to"] > since]
    return df


def hourly_average(df):
    """Average across stations for each hour, with the number of stations reporting."""
    if df.empty:
        return pd.DataFrame(columns=["hour_local", "mean", "stations"])
    out = (df.groupby("hour_local")
             .agg(mean=("value", "mean"), stations=("location_id", "nunique"))
             .reset_index()
             .sort_values("hour_local"))
    return out


def daily_station_means(df):
    """24-hour mean per station per Sydney local day, for days with enough hours."""
    if df.empty:
        return pd.DataFrame(columns=["day", "location_id", "location_name", "mean", "hours"])
    df = df.assign(day=df["hour_local"].dt.date)
    out = (df.groupby(["day", "location_id", "location_name"])
             .agg(mean=("value", "mean"), hours=("value", "size"))
             .reset_index())
    return out[out["hours"] >= MIN_HOURS_PER_DAY].sort_values(["day", "location_name"])


def latest_snapshot(df):
    """The most recent hour with data: (hour_local, mean, stations), or None."""
    hourly = hourly_average(df)
    if hourly.empty:
        return None
    last = hourly.iloc[-1]
    return last["hour_local"], float(last["mean"]), int(last["stations"])


def last_24h_by_station(df, locations):
    """Each station's mean over the 24 hours up to the latest hour in the data."""
    if df.empty:
        return pd.DataFrame(columns=["location_id", "location_name", "mean", "hours",
                                     "latitude", "longitude"])
    end = df["datetime_to"].max()
    recent = df[df["datetime_to"] > end - pd.Timedelta(hours=24)]
    out = (recent.groupby(["location_id", "location_name"])
                 .agg(mean=("value", "mean"), hours=("value", "size"))
                 .reset_index())
    coords = locations[["location_id", "latitude", "longitude"]]
    return out.merge(coords, on="location_id", how="left").sort_values("mean", ascending=False)


def pipeline_health(runs, measurements):
    """Summary numbers for the pipeline status panel."""
    if runs is None or runs.empty:
        return None
    runs = runs.copy()
    runs["run_at"] = pd.to_datetime(runs["run_at"], utc=True)
    ok = runs[runs["status"] == "success"]
    rows = measurements.assign(datetime_to=measurements["datetime_to"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    filled, expected = completeness(rows.to_dict("records")) if len(rows) else (0, 0)
    return {
        "runs": len(runs),
        "succeeded": len(ok),
        "last_run": runs["run_at"].max(),
        "last_success": ok["run_at"].max() if len(ok) else None,
        "last_status": runs.sort_values("run_at").iloc[-1]["status"],
        "completeness": filled / expected if expected else None,
    }
