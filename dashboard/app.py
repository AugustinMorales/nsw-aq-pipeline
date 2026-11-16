"""
NSW air quality dashboard.

Shows the data collected by the pipeline: the latest readings, hourly trend,
24-hour averages by station against the WHO guideline, and a station map,
plus the pipeline's own health.

Run locally from the repository root:
    streamlit run dashboard/app.py
"""
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os

import streamlit as st

import data

REPO_URL = "https://github.com/AugustinMorales/nsw-aq-pipeline"
LOAD_DAYS = 31  # loaded once and cached; the period filter slices it
PERIODS = {"24 hours": 1, "7 days": 7, "30 days": 30}

st.set_page_config(page_title="NSW Air Quality", layout="wide")

# Colors from the validated reference palette (series slot 1, sequential blue
# ramp, muted ink for reference lines). Text keeps the theme's own ink.
DARK = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
SERIES = "#3987e5" if DARK else "#2a78d6"
MUTED = "#898781"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


@st.cache_data(ttl=15 * 60, show_spinner="Loading the latest data...")
def load_all():
    """Everything the dashboard needs, refreshed at most every 15 minutes."""
    measurements = data.load_measurements(LOAD_DAYS)
    locations = data.read_csv("locations.csv")
    if locations is None:
        locations = data.locations_from_measurements(measurements)
    runs = data.read_csv("runs.csv")
    return measurements, locations, runs


def fmt_time(ts):
    """e.g. 'Sun 8 Nov, 3:00 pm' in Sydney time (works on Windows and Linux)."""
    ts = pd.Timestamp(ts).tz_convert(data.LOCAL_TZ)
    hour = ts.strftime("%I").lstrip("0")
    return f"{ts.strftime('%a')} {ts.day} {ts.strftime('%b')}, {hour}:{ts.strftime('%M')} {ts.strftime('%p').lower()}"


def ago(ts):
    minutes = int((datetime.now(timezone.utc) - pd.Timestamp(ts).to_pydatetime()).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def style(fig, height):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8),
                      showlegend=False, hoverlabel=dict(font_size=13))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridwidth=1, zeroline=False)
    return fig


# ---------------------------------------------------------------- data

try:
    measurements, locations, runs = load_all()
except Exception as err:  # network or file problem: say so instead of crashing
    st.error(f"Couldn't load the data ({type(err).__name__}: {err}). Try again in a few minutes.")
    st.stop()

# ---------------------------------------------------------------- header and filters

st.title("NSW air quality")
st.caption(
    "Hourly readings from the New South Wales government monitoring network, collected "
    f"every 3 hours by the [nsw-aq-pipeline]({REPO_URL}). Times are Sydney local time."
)

f1, f2, f3 = st.columns([2, 3, 2])
region = f1.selectbox("Region", data.region_options(locations), index=0)
parameter = f2.segmented_control(
    "Pollutant", list(data.POLLUTANTS), default="pm25",
    format_func=lambda p: data.POLLUTANTS[p][0]) or "pm25"
period = f3.segmented_control("Period", list(PERIODS), default="7 days") or "7 days"

label, unit = data.POLLUTANTS[parameter]
scale = 1.0
if unit == "ppm":  # gases read more easily in parts per billion
    scale, unit = 1000.0, "ppb"
guideline = data.WHO_24H.get(parameter)

station_ids = data.station_ids_in(region, locations)
since = measurements["datetime_to"].max() - pd.Timedelta(days=PERIODS[period]) \
    if not measurements.empty else None
df = data.select(measurements, parameter, station_ids, since)
df = df.assign(value=df["value"] * scale)
# 24-hour figures always use the last full day of data, whatever the period
last_day = data.select(measurements, parameter, station_ids).assign(
    value=lambda d: d["value"] * scale)

if df.empty:
    st.info(f"No {label} data for {region} in the last {period}. "
            "Try another region or pollutant, or a longer period.")
    st.stop()

snapshot = data.latest_snapshot(df)
by_station = data.last_24h_by_station(last_day, locations)
full_days = by_station[by_station["hours"] >= data.MIN_HOURS_PER_DAY]
health = data.pipeline_health(runs, measurements)

# ---------------------------------------------------------------- headline numbers

k1, k2, k3, k4 = st.columns(4)
hour, mean, n_stations = snapshot
k1.metric(f"Latest hourly average, {label}", f"{mean:.1f} {unit}", border=True,
          help=f"Average across reporting stations for the hour starting {fmt_time(hour)}.")
k2.metric("Stations reporting", f"{n_stations} of {len(station_ids & set(measurements['location_id']))}",
          border=True, help="Stations with a reading in the latest hour.")
if guideline is not None:
    over = int((full_days["mean"] > guideline).sum())
    k3.metric("Stations above WHO 24-hour guideline", f"{over} of {len(full_days)}", border=True,
              help=f"24-hour average above {guideline:g} {unit} (WHO 2021), over the last 24 hours.")
elif not full_days.empty:
    top = full_days.iloc[0]
    k3.metric("Highest 24-hour average", f"{top['mean']:.1f} {unit}", border=True,
              help=f"{data.display_name(top['location_name'])}, over the last 24 hours.")
else:
    k3.metric("Highest 24-hour average", "–", border=True)
if health and health["completeness"] is not None:
    k4.metric("Data completeness", f"{health['completeness']:.1%}", border=True,
              help="Share of hourly slots filled, for every station and pollutant in the last 31 days.")
else:
    k4.metric("Data completeness", "–", border=True)

# ---------------------------------------------------------------- hourly trend

hourly = data.hourly_average(df)
st.subheader(f"Hourly {label}, average across {region} stations")
fig = go.Figure(go.Scatter(
    x=hourly["hour_local"], y=hourly["mean"], mode="lines",
    line=dict(color=SERIES, width=2),
    customdata=hourly["stations"],
    hovertemplate=f"%{{x|%a %d %b, %H:%M}}<br><b>%{{y:.1f}} {unit}</b><br>%{{customdata}} stations<extra></extra>",
))
fig.update_yaxes(title_text=unit, rangemode="tozero")
fig.update_layout(hovermode="x")
st.plotly_chart(style(fig, 320), config={"displayModeBar": False})
if guideline is not None:
    st.caption(f"The WHO guideline ({guideline:g} {unit}) applies to 24-hour averages, "
               "so it's shown on the station chart below rather than against hourly values.")

# ---------------------------------------------------------------- stations: ranked bars and map

left, right = st.columns([3, 2])
with left:
    st.subheader("24-hour average by station")
    if full_days.empty:
        st.info("No station has 18 or more hours of data in the last 24 hours yet.")
    else:
        ranked = full_days.sort_values("mean")
        names = ranked["location_name"].map(data.display_name)
        bars = go.Figure(go.Bar(
            x=ranked["mean"], y=names, orientation="h",
            marker=dict(color=SERIES, cornerradius=4), width=0.7,
            customdata=ranked["hours"],
            hovertemplate=f"%{{y}}<br><b>%{{x:.1f}} {unit}</b><br>%{{customdata}} of 24 hours<extra></extra>",
        ))
        if guideline is not None:
            bars.add_vline(x=guideline, line=dict(color=MUTED, width=1.5, dash="dash"),
                           annotation_text=f"WHO {guideline:g}", annotation_position="top",
                           annotation_font_color=MUTED)
        bars.update_xaxes(title_text=unit, rangemode="tozero", showgrid=True)
        bars.update_yaxes(showgrid=False)
        style(bars, max(260, 22 * len(ranked) + 80))
        bars.update_layout(margin=dict(t=32))  # room for the WHO label above the plot
        st.plotly_chart(bars, config={"displayModeBar": False})
        partial = len(by_station) - len(full_days)
        if partial:
            st.caption(f"{partial} station(s) with fewer than {data.MIN_HOURS_PER_DAY} hours "
                       "of data in the last 24 hours are left out.")

with right:
    st.subheader("Station map")
    mapped = full_days.dropna(subset=["latitude", "longitude"])
    if mapped.empty:
        st.info("No stations to map yet.")
    else:
        mapped = mapped.assign(name=mapped["location_name"].map(data.display_name))
        m = px.scatter_map(
            mapped, lat="latitude", lon="longitude", color="mean",
            color_continuous_scale=BLUE_RAMP, hover_name="name",
            hover_data={"mean": ":.1f", "latitude": False, "longitude": False},
            labels={"mean": f"24-h avg ({unit})"}, zoom=8 if region == data.SYDNEY else 5,
            map_style=os.environ.get("MAP_STYLE") or ("carto-darkmatter" if DARK else "carto-positron"),
        )
        m.update_traces(marker=dict(size=13))
        m.update_layout(height=420, margin=dict(l=0, r=0, t=0, b=0),
                        coloraxis_colorbar=dict(title=unit, thickness=12))
        st.plotly_chart(m, config={"displayModeBar": False})

# ---------------------------------------------------------------- table view

with st.expander("Table view and download"):
    daily = data.daily_station_means(df)
    table = daily.assign(station=daily["location_name"].map(data.display_name))[["day", "station", "mean", "hours"]]
    st.caption(f"Daily averages per station ({label}, {unit}), Sydney local days with "
               f"{data.MIN_HOURS_PER_DAY}+ hours of data.")
    st.dataframe(
        table.sort_values(["day", "station"], ascending=[False, True]), hide_index=True,
        column_config={"day": "Day", "station": "Station",
                       "mean": st.column_config.NumberColumn(f"Average ({unit})", format="%.1f"),
                       "hours": "Hours"})
    hourly_rows = df.assign(hour_start=df["hour_local"].dt.strftime("%Y-%m-%d %H:%M"))
    st.download_button(
        "Download hourly data (CSV)",
        hourly_rows[["hour_start", "location_id", "location_name", "value"]]
        .rename(columns={"value": f"{parameter}_{unit}"}).to_csv(index=False),
        file_name=f"nsw_{parameter}_{region.lower().replace(' ', '_')}_{period.replace(' ', '')}.csv",
        mime="text/csv")

# ---------------------------------------------------------------- pipeline status

st.divider()
st.subheader("Pipeline status")
if health is None:
    st.write("No pipeline runs recorded yet.")
else:
    s1, s2, s3 = st.columns(3)
    s1.metric("Last successful run",
              ago(health["last_success"]) if health["last_success"] is not None else "never",
              help=fmt_time(health["last_success"]) if health["last_success"] is not None else None)
    s2.metric("Runs succeeded", f"{health['succeeded']} of {health['runs']}",
              help="Since the pipeline started. Includes early test runs.")
    s3.metric("Latest run", health["last_status"], help=fmt_time(health["last_run"]))
    st.caption(f"Runs every 3 hours on GitHub Actions. Details: [run history]({REPO_URL}/actions) · "
               f"[run log]({REPO_URL}/blob/main/data/runs.csv) · [monitoring notes]({REPO_URL}/blob/main/MONITORING.md)")

st.caption("Data: © State of New South Wales and Department of Planning, Industry and Environment, "
           "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). "
           "Readings are as published and may change after quality checks.")
