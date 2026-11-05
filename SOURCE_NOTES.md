# NSW Air Quality API: source notes and field mapping

Source: NSW Department of Climate Change, Energy, the Environment and Water (air quality monitoring network)
OpenAQ status: provider 153 "Australia - New South Wales", last data 2022-11-22

## 1. Access

| Item | Value |
| --- | --- |
| Base URL | `https://data.airquality.nsw.gov.au/api/Data/` |
| Sites | `GET get_SiteDetails` |
| Parameters | `GET get_ParameterDetails` |
| Observations | `POST get_Observations`, JSON body (empty body = latest hour, all sites and parameters) |
| Authentication | None |
| Format | JSON |
| Targeted request | `{"Parameters": ["PM2.5"], "Sites": [39], "StartDate": "2026-10-03", "EndDate": "2026-10-06", "Categories": ["Averages"], "SubCategories": ["Hourly"], "Frequency": ["Hourly average"]}` works (HTTP 200). **`EndDate` is exclusive**: this request returned Oct 3, 4 and 5 |
| Update schedule | Hourly, about 1 hour after each hour ends. In the sample run at 22:08 UTC, the latest hour ended at 21:00 UTC |
| Rate limits | Not documented; keep requests small and infrequent |
| License | CC BY 4.0. Attribution: "© State of New South Wales and Department of Planning, Industry and Environment" |
| Documentation | [API user guide (PDF)](https://www.environment.nsw.gov.au/sites/default/files/air-quality-application-programming-interface-user-guide-210346.pdf) |

## 2. What the sample contained (2026-10-31 22:08 UTC)

- **139 sites.** 17 are regional groupings (IDs 10000000 to 170000000) with no coordinates, and one is "Test Site" (805), leaving 121 usable sites.
- **1,195 observation records** for a single hour. Every record has the same keys: `Site_Id`, `Parameter` (nested), `Date`, `Hour`, `HourDescription`, `Value`, `AirQualityCategory`, `DeterminingPollutant`.
- **27 kinds of record.** The same pollutant appears with several `Frequency` values (for example PM2.5 as both an hourly average and a 24-hour rolling average), plus air-quality categories, weather readings, and DustWatch readings.

Pollutant records (hourly averages only):

| NSW code | Units | Sites | Nulls | Negative values | Range |
| --- | --- | --- | --- | --- | --- |
| PM2.5 | µg/m³ | 49 | 2 | 4 | -5.8 to 16.4 |
| PM10 | µg/m³ | 59 | 2 | 0 | 2.5 to 41.7 |
| NO2 | pphm | 45 | 3 | 3 | -0.09 to 1.82 |
| OZONE | pphm | 45 | 4 | 0 | 0.40 to 2.93 |
| SO2 | pphm | 37 | 7 | 2 | -0.01 to 0.47 |
| CO | ppm | 37 | 13 | 8 | -0.14 to 0.30 |

## 3. Field mapping to OpenAQ

| OpenAQ concept | NSW source | Rule |
| --- | --- | --- |
| Location ID | `Site_Id` | Use as-is, prefixed (for example `nsw-39`) to avoid clashes |
| Location name | `SiteName` | Trim whitespace (some names have trailing spaces) |
| Latitude / longitude | `Latitude`, `Longitude` from `get_SiteDetails` | Join on `Site_Id`; reject sites with null coordinates |
| Sensor | `Site_Id` + `ParameterCode` | One sensor per pollutant per site |
| Pollutant | `Parameter.ParameterCode` | PM2.5 → pm25, PM10 → pm10, NO2 → no2, OZONE → o3, SO2 → so2, CO → co |
| Value | `Value` | Converted to OpenAQ units (below) |
| Unit | `Parameter.Units` | µg/m³ unchanged; **pphm → ppm by dividing by 100**; ppm unchanged |
| Timestamp | `Date` + `Hour` | `Hour` is the hour *ending* (Hour 7 = "6 am - 7 am", Hour 24 = "11 pm - midnight"). Times are AEST (UTC+10) all year. Period end in UTC = `Date` 00:00 + `Hour` hours − 10 hours; period start = end − 1 hour. Example: 2026-11-01, Hour 7 → 2026-10-31 20:00 to 21:00 UTC |
| Averaging period | `Parameter.Frequency` | Keep only "Hourly average" (1 hour) |

## 4. Records to filter out

- Non-measurement records: `AQC`, `RAQC`, `RAQI` (categories and indexes)
- Weather: `TEMP`, `HUMID`, `RAIN`, `SOLAR`, `WDR`, `WSP`, `SD1`
- Not a concentration: `NEPH` (light scattering, units 10^-4 m^-1)
- Derived values: any `Frequency` other than "Hourly average" (rolling 4h, 8h and 24h averages)
- Category not "Averages" (Maximums, Exceedences)
- Pseudo-sites with null coordinates, and "Test Site" (805)
- Null `Value`

## 5. Decisions

1. **Time zone: AEST (UTC+10) all year.** `check_timezone.py` requested PM2.5 at Rozelle for 3–5 October 2026; 4 October (when NSW clocks skipped from 2 am to 3 am) still had all 24 hours, so the data ignores daylight saving.
2. **Negative values: kept as reported, within limits.** Small negatives near zero are normal instrument noise for reference monitors, and OpenAQ's criteria ask for raw values. Values below a per-pollutant floor (for example −10 µg/m³ for PM, −0.01 ppm for NO2) are rejected as `out_of_range`. The limits are in `VALID_RANGES` in `nsw_pipeline/normalize.py`.
3. **DustWatch (`PM2.5d`, `PM10d`, `TSPd`): excluded for now.** These come from a separate dust-monitoring network at about 40 mostly rural sites, reported as whole numbers, and may not be reference-grade. Adding them later means adding the codes to `PARAMETERS`.
4. **Ammonia (`NH3`) and nitric oxide (`NO`): excluded.** Outside OpenAQ's core pollutant list.

## 6. Still to confirm against the live API

- **Large requests time out.** On 2026-11-04, one request for all six pollutants at 121 sites over two days got no response within 90 seconds. Split into one pollutant and 30 sites at a time, the same data took 30 requests and 890 seconds; busy Sydney batches took 50–114 seconds each, with PM10 and PM2.5 slowest and CO fastest. Batches of rural sites returned nothing, because those sites don't measure these pollutants. The pipeline now uses batches of 15, asks only the sites that report each pollutant in the latest hour, and looks back 12 hours instead of 24. Per-request timings are logged so `SITES_PER_REQUEST` in `nsw_pipeline/client.py` can be tuned.
- **Requests return future hours.** The API takes whole dates, so a request that includes today returns all 24 of today's hours, with blank values for hours that haven't happened. In the 2026-11-04 run these were 4,352 of the 5,686 records first logged as `missing_value`. They are now skipped. The other 1,334 were genuinely missing readings (about 15% of past hourly slots).
- Whether the API allows requests from GitHub Actions servers. The first manual workflow run will show this.

## 7. The API sometimes returns incomplete data

Two runs 23 minutes apart on 2026-11-04 (raw responses `20261104T230304Z` and `20261104T232618Z`) requested the same sites and dates. Comparing all 13,056 records:

- Where both runs had a value for the same site, pollutant and hour, the values were identical (0 differences). NSW was not revising or withdrawing data.
- 2,383 values present in the first run were blank in the second. Most were long stretches in the middle of a series: for example, Rozelle PM10 was blank from 9 am to midnight on 4 November but present from 1 am to 8 am on 5 November. Real data cannot disappear and return like that.
- The first run also had blanks (465 before a later reading in the same series) that the second run filled in.

**Conclusion:** the API intermittently leaves out readings it has. The pipeline therefore treats a blank as "not available in this response" rather than a rejected record, never lets a blank overwrite a stored value, and relies on the overlapping lookback (each hour is requested by about four runs) to fill gaps. Combining just these two runs filled 96.8% of hourly slots. The monitoring report tracks this completeness figure over the month.
