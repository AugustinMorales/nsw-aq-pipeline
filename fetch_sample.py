"""
One-time sample downloader for the NSW Air Quality API.

Saves the raw responses to the raw/ folder so we can inspect the real data
format before designing the pipeline. Uses only Python's standard library,
and the NSW API needs no key.

API user guide:
https://www.environment.nsw.gov.au/sites/default/files/air-quality-application-programming-interface-user-guide-210346.pdf

Usage: python fetch_sample.py
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://data.airquality.nsw.gov.au/api/Data/"
RAW_DIR = Path("raw")
TIMEOUT_S = 60


def request(endpoint, body=None):
    """GET (no body) or POST (JSON body). Returns (status, text)."""
    url = BASE_URL + endpoint
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", errors="replace")


def save_and_summarize(name, status, text, stamp):
    """Save the raw response and print what it contains."""
    path = RAW_DIR / f"{stamp}_{name}.json"
    path.write_text(text, encoding="utf-8")
    print(f"\n=== {name}: HTTP {status}, {len(text):,} characters, saved to {path}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("Response is not JSON. First 300 characters:")
        print(text[:300])
        return None
    if isinstance(parsed, list):
        print(f"Records: {len(parsed)}")
        if parsed:
            print("First record:")
            print(json.dumps(parsed[0], indent=2)[:1500])
    else:
        print("Response (first 1500 characters):")
        print(json.dumps(parsed, indent=2)[:1500])
    return parsed


def main():
    RAW_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # 1. Monitoring sites (locations)
    save_and_summarize("sites", *request("get_SiteDetails"), stamp)

    # 2. Parameters (pollutants, units, valid category/frequency combinations)
    save_and_summarize("parameters", *request("get_ParameterDetails"), stamp)

    # 3. Observations. Per the user guide, an empty body returns the current
    #    hourly data for all sites and parameters, so it is always a valid request.
    save_and_summarize("observations_current", *request("get_Observations", ""), stamp)

    print("\nDone. Send the printed output (or the files in raw/) back for review.")


if __name__ == "__main__":
    main()
