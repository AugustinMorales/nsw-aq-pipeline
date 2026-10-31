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
| Update schedule | Hourly. In the sample run at 22:08 UTC, the latest hour was "6 am - 7 am" on the following local date |
| Rate limits | Not documented; keep requests small and infrequent |
| License | CC BY 4.0. Attribution: "© State of New South Wales and Department of Planning, Industry and Environment" |
| Documentation | [API user guide (PDF)](https://www.environment.nsw.gov.au/sites/default/files/air-quality-application-programming-interface-user-guide-210346.pdf) |

## 2. What the sample contained (2026-10-31 22:08 UTC)

- **139 sites.** 16 are regional groupings (IDs 10000000 to 170000000) with no coordinates, and one is "Test Site" (805).
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
| Timestamp | `Date` + `Hour` | `Hour` is the hour *ending* (Hour 7 = "6 am - 7 am"). Store the period start and end in UTC. Time zone still to be confirmed (see open questions) |
| Averaging period | `Parameter.Frequency` | Keep only "Hourly average" (1 hour) |

## 4. Records to filter out

- Non-measurement records: `AQC`, `RAQC`, `RAQI` (categories and indexes)
- Weather: `TEMP`, `HUMID`, `RAIN`, `SOLAR`, `WDR`, `WSP`, `SD1`
- Not a concentration: `NEPH` (light scattering, units 10^-4 m^-1)
- Derived values: any `Frequency` other than "Hourly average" (rolling 4h, 8h and 24h averages)
- Category not "Averages" (Maximums, Exceedences)
- Pseudo-sites with null coordinates, and "Test Site" (805)
- Null `Value`

## 5. Open questions

1. **Time zone.** Are `Date`/`Hour` in local clock time (AEDT in summer, UTC+11) or Australian Eastern Standard Time all year (UTC+10)? `check_timezone.py` tests this using the 4 October 2026 daylight saving changeover.
2. **Negative values.** Small negatives near zero are normal instrument noise for reference monitors, and the source publishes them. Decide whether to keep them as reported (OpenAQ's criteria ask for raw values) or flag them. Large negatives such as PM2.5 at -5.8 deserve a validation threshold.
3. **DustWatch (`PM2.5d`, `PM10d`, `TSPd`).** These come from a separate dust-monitoring network at about 40 mostly rural sites, reported as whole numbers. They may not be reference-grade, so exclude them at first and decide later.
4. **Ammonia (`NH3`) and nitric oxide (`NO`).** Outside OpenAQ's core pollutant list; exclude at first.
