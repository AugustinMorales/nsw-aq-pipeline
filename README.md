NSW Air Quality Ingestion Pipeline

A data pipeline that collects hourly air-quality measurements from the New South Wales (Australia) government monitoring network and converts them to the structure used by OpenAQ.

OpenAQ previously carried NSW data (provider 153, "Australia - New South Wales"), but its last NSW measurements are from November 2022. This project aims to fill that gap and keep the data flowing reliably over time.

Status: in progress. The source has been researched, sampled, and mapped to OpenAQ concepts. The normalization, validation, and scheduled collection steps are still to be built.

Data source
Item	Value
Provider	NSW government air quality monitoring network
API	https://data.airquality.nsw.gov.au/api/Data/ (open, no API key)
Format	JSON
Updates	Hourly, about one hour after each hour ends
Pollutants used	PM2.5, PM10, NO2, O3, SO2, CO
License	CC BY 4.0
Documentation	NSW Air Quality API user guide (PDF)

Data attribution: © State of New South Wales and Department of Planning, Industry and Environment.

See SOURCE_NOTES.md for the full source notes, the field mapping to OpenAQ, and the records the pipeline filters out.

Key findings so far
Timestamps are Australian Eastern Standard Time (UTC+10) all year, with no daylight saving. Confirmed by checking that 4 October 2026, the day NSW clocks skipped an hour, still has all 24 hourly records.
Gas units need converting. NO2, O3, and SO2 are reported in pphm (parts per hundred million) and are divided by 100 to get ppm.
Most records are not pollutant measurements. Each hourly response includes weather readings, air-quality categories, and rolling averages, which the pipeline filters out.
EndDate in observation requests is exclusive, so a request for 3 to 6 October returns 3, 4, and 5 October.
Requirements
Python 3.11 or newer
No extra packages so far (the scripts use only the Python standard library)
No API key
Usage

Run these from the project folder.

Download a sample of the current data (sites, parameters, and the latest hour of observations) into raw/:

python fetch_sample.py

Check how the API's timestamps handle daylight saving:

python check_timezone.py
Files
File	Purpose
fetch_sample.py	One-time downloader that saves raw API responses for inspection
check_timezone.py	Test that confirmed the API uses AEST all year
SOURCE_NOTES.md	Source documentation, sample analysis, and field mapping
raw/	Saved sample responses, used for inspection and (later) tests
Roadmap
Choose a source not currently carried by OpenAQ
Document the source (endpoints, format, schedule, license)
Map source fields to OpenAQ concepts
Download and inspect a sample
Normalize and validate records (unit and timestamp conversion, rejected-record logging)
Add tests using the saved samples
Make it repeatable: retries, logging, duplicate protection, scheduled runs
Run and monitor for one month
