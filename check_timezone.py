"""
Find out whether NSW timestamps are local clock time or standard time (AEST).

NSW daylight saving started at 2 am on Sunday 4 October 2026, when clocks
jumped straight to 3 am. If the API uses local clock time, the hour
"2 am - 3 am" doesn't exist that day, so Oct 4 has only 23 hours. If it
uses Australian Eastern Standard Time all year, Oct 4 has all 24 hours.

This also tests a targeted get_Observations request (specific site,
pollutant and dates), which the real pipeline will need.

Usage: python check_timezone.py
"""
import json
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://data.airquality.nsw.gov.au/api/Data/get_Observations"
SITE_ID = 39  # ROZELLE, reports PM2.5 hourly
BODY = {
    "Parameters": ["PM2.5"],
    "Sites": [SITE_ID],
    "StartDate": "2026-10-03",
    "EndDate": "2026-10-06",
    "Categories": ["Averages"],
    "SubCategories": ["Hourly"],
    "Frequency": ["Hourly average"],
}


def main():
    req = urllib.request.Request(
        URL,
        data=json.dumps(BODY).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, text = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read().decode("utf-8", errors="replace")

    Path("raw").mkdir(exist_ok=True)
    out = Path("raw") / "dst_check_2026-10-04.json"
    out.write_text(text, encoding="utf-8")
    print(f"HTTP {status}, {len(text):,} characters, saved to {out}")
    if status != 200:
        print("Request failed. Response:")
        print(text[:1000])
        return

    records = json.loads(text)
    print(f"Records: {len(records)}")
    hours_by_date = defaultdict(list)
    for r in records:
        hours_by_date[r["Date"]].append(r["Hour"])

    for day in sorted(hours_by_date):
        hours = sorted(hours_by_date[day])
        missing = sorted(set(range(1, 25)) - set(hours))
        dupes = sorted({h for h in hours if hours.count(h) > 1})
        print(f"{day}: {len(hours)} hourly records, hours {hours[0]}-{hours[-1]}, "
              f"missing {missing or 'none'}, duplicated {dupes or 'none'}")

    oct4 = hours_by_date.get("2026-10-04", [])
    print()
    if len(oct4) == 24:
        print("Oct 4 has all 24 hours: timestamps look like standard time (AEST, UTC+10) all year.")
    elif len(oct4) == 23 and 3 not in oct4:
        print("Oct 4 is missing the 2-3 am hour: timestamps look like local clock time (daylight saving applies).")
    else:
        print("Result is unclear; send this output back for a closer look.")


if __name__ == "__main__":
    main()
