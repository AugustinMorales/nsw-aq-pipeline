# NSW Air Quality Ingestion Pipeline

A data pipeline that collects hourly air-quality measurements from the New South Wales (Australia) government monitoring network and converts them to the structure used by [OpenAQ](https://openaq.org/).

OpenAQ previously carried NSW data (provider 153, "Australia - New South Wales"), but its last NSW measurements are from November 2022. This project fills that gap and keeps the data flowing reliably over time.

## Data source

| Item | Value |
| --- | --- |
| Provider | NSW government air quality monitoring network |
| API | `https://data.airquality.nsw.gov.au/api/Data/` (open, no API key) |
| Format | JSON |
| Updates | Hourly, about one hour after each hour ends |
| Pollutants collected | PM2.5, PM10, NO2, O3, SO2, CO |
| License | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Documentation | [NSW Air Quality API user guide (PDF)](https://www.environment.nsw.gov.au/sites/default/files/air-quality-application-programming-interface-user-guide-210346.pdf) |

Data attribution: © State of New South Wales and Department of Planning, Industry and Environment.

See [SOURCE_NOTES.md](SOURCE_NOTES.md) for the full source notes and the field mapping to OpenAQ.

## How it works

Each run:

1. **Fetches** the site list, then the latest hour of data for every site (one quick request) to see which sites measure which pollutants. It then requests the last 12 hours of hourly averages, one pollutant and 30 sites at a time, asking only the sites that measure that pollutant (about 12 requests). One request for everything is too slow for the NSW server. Network errors, timeouts, and server errors are retried up to 3 times with increasing waits. If a piece still fails, the rest of the run continues and the run is marked `partial`; the next run's lookback usually fills the gap. The run stops starting new requests after 25 minutes.
2. **Saves the raw responses** (gzipped) so any run can be investigated or reprocessed.
3. **Normalizes** each record to OpenAQ concepts: location, pollutant, value, unit, and UTC time period. NSW times are AEST (UTC+10) all year, and gases are converted from pphm to ppm.
4. **Validates** each record and sorts it into one of three groups:
   - **accepted:** stored as a measurement
   - **rejected:** a pollutant we collect, but invalid (unknown site, missing coordinates, unexpected unit, bad timestamp, non-numeric value, or a value outside the plausible range); logged with the reason
   - **blank:** no value for a past hour. The API sometimes leaves out readings it returned minutes earlier, so a blank is not proof the reading is missing. Blanks are counted in the run log, never overwrite stored values, and are left for later runs to fill.
   - **skipped:** out of scope by design (weather, air-quality categories, rolling averages), or an hour that hadn't ended yet. The API only accepts whole dates, so a request for today also returns today's future hours with blank values.
5. **Stores** measurements in `data/measurements/YYYY-MM-DD.csv` (one file per UTC day), merging by location, pollutant, and hour, so overlapping runs never create duplicates. If NSW revises a value, the stored value is updated.
6. **Logs the run** in `data/runs.csv`, whether it succeeded or failed.

The monitoring report's **completeness** figure shows how many hourly slots are filled for each station and pollutant, from its first to its last stored hour. It measures how well the repeated runs make up for incomplete API responses.

On GitHub, the [collect workflow](.github/workflows/collect.yml) runs this every 3 hours and commits the new data. Because each run looks back 12 hours, every hour gets about four chances to be collected, so a late or failed run is filled in by the next one.

### Validation rules

| Pollutant | Unit | Accepted range |
| --- | --- | --- |
| PM2.5 | µg/m³ | −10 to 2,000 |
| PM10 | µg/m³ | −10 to 3,000 |
| NO2 | ppm | −0.01 to 1 |
| O3 | ppm | −0.01 to 0.5 |
| SO2 | ppm | −0.01 to 2 |
| CO | ppm | −0.5 to 50 |

Small negative values are kept as reported: reference monitors produce them near zero, and OpenAQ asks for raw values. The ranges only reject values that point to an instrument fault. They are set in `nsw_pipeline/normalize.py`.

## Output format

`data/measurements/YYYY-MM-DD.csv`:

| Column | Example | Notes |
| --- | --- | --- |
| `location_id` | `nsw-39` | NSW `Site_Id` with a prefix |
| `location_name` | `ROZELLE` | |
| `latitude`, `longitude` | `-33.86433`, `151.16395` | |
| `parameter` | `no2` | OpenAQ parameter name |
| `value`, `unit` | `0.01817372`, `ppm` | Converted to OpenAQ units |
| `datetime_from`, `datetime_to` | `2026-10-31T20:00:00Z`, `2026-10-31T21:00:00Z` | The hour, in UTC |
| `source_site_id`, `source_parameter`, `source_value`, `source_unit` | `39`, `NO2`, `1.817372`, `pphm` | The original values, for traceability |
| `fetched_at` | `2026-10-31T22:08:15Z` | When the value was first collected |

Rejected records go to `data/rejected/YYYY-MM-DD.csv` with the reason.

## Requirements

- Python 3.11 or newer
- No packages needed to run the pipeline (standard library only)
- `pytest` for the tests
- No API key

## Usage

Run these from the project folder.

Run the pipeline once (writes to `data/` and `raw/runs/`):

```
python -m nsw_pipeline.run
```

Print the monitoring report:

```
python -m nsw_pipeline.report
```

Run the tests:

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests use real API responses saved in `tests/fixtures/`, so they need no network access.

## Files

| Path | Purpose |
| --- | --- |
| `nsw_pipeline/client.py` | API requests with retries |
| `nsw_pipeline/normalize.py` | Conversion to OpenAQ concepts, and validation rules |
| `nsw_pipeline/storage.py` | CSV storage with duplicate protection, and the run log |
| `nsw_pipeline/run.py` | One complete pipeline run |
| `nsw_pipeline/report.py` | Monitoring report |
| `tests/` | Tests, with saved API responses in `tests/fixtures/` |
| `.github/workflows/collect.yml` | Scheduled collection every 3 hours |
| `.github/workflows/tests.yml` | Runs the tests on every push |
| `data/` | Collected measurements, rejected records, and the run log |
| `MONITORING.md` | Log of the month-long monitoring period |
| `SOURCE_NOTES.md` | Source documentation and field mapping |
| `fetch_sample.py`, `check_timezone.py`, `raw/` | Early research scripts and their saved samples |

## Roadmap

1. ~~Choose a source not currently carried by OpenAQ~~
2. ~~Document the source (endpoints, format, schedule, license)~~
3. ~~Map source fields to OpenAQ concepts~~
4. ~~Download and inspect a sample~~
5. ~~Normalize and validate records~~
6. ~~Add tests using the saved samples~~
7. ~~Make it repeatable: retries, logging, duplicate protection, scheduled runs~~
8. Run and monitor for one month (see [MONITORING.md](MONITORING.md))
