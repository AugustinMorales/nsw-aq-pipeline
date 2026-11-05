"""
HTTP client for the NSW Air Quality API, with retries.

Uses only the standard library. Transient failures (network errors,
timeouts, HTTP 5xx, 408 and 429, unreadable JSON) are retried with
exponential backoff; other 4xx errors mean the request itself is wrong,
so they fail immediately.
"""
import json
import logging
import time
import urllib.error
import urllib.request

from .normalize import CATEGORY, FREQUENCY, PARAMETERS, SUB_CATEGORY

BASE_URL = "https://data.airquality.nsw.gov.au/api/Data/"
RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}
# Request time depends little on the number of sites: batches of 15 took
# 45-114 s, about the same as batches of 30, so fewer, larger batches are
# faster overall. Two days of one pollutant for 30 sites took up to 114 s.
SITES_PER_REQUEST = 30
TIME_BUDGET_S = 25 * 60  # stop starting new requests after 25 minutes

log = logging.getLogger(__name__)


class FetchError(Exception):
    """The API could not be reached or returned an unusable response."""


def fetch_json(endpoint, body=None, *, retries=3, backoff_s=10.0, timeout_s=180,
               opener=urllib.request.urlopen, sleep=time.sleep):
    """GET (body is None) or POST JSON to an endpoint.

    Returns (parsed_json, raw_text). Raises FetchError when all attempts fail.
    """
    url = BASE_URL + endpoint
    data = None
    headers = {"Accept": "application/json", "User-Agent": "nsw-aq-pipeline"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url, data=data, headers=headers, method="GET" if body is None else "POST")
        try:
            with opener(request, timeout=timeout_s) as response:
                text = response.read().decode("utf-8")
            return json.loads(text), text
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")[:300] if err.fp else ""
            last_error = f"HTTP {err.code}: {detail}"
            if err.code not in RETRYABLE_HTTP:
                raise FetchError(f"{endpoint} rejected the request: {last_error}") from err
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as err:
            last_error = f"{type(err).__name__}: {err}"

        if attempt < retries:
            wait = backoff_s * 2 ** (attempt - 1)
            log.warning("%s attempt %d/%d failed (%s); retrying in %.0fs",
                        endpoint, attempt, retries, last_error, wait)
            sleep(wait)

    raise FetchError(f"{endpoint} failed after {retries} attempts: {last_error}")


def fetch_sites(**kwargs):
    """All monitoring sites. Returns (sites, raw_text)."""
    sites, text = fetch_json("get_SiteDetails", **kwargs)
    if not isinstance(sites, list):
        raise FetchError("get_SiteDetails did not return a list")
    return sites, text


def observations_body(site_ids, start_date, end_date_exclusive, parameters=None):
    """Request body for hourly averages of the given pollutants (default: all we collect).

    The API treats EndDate as exclusive.
    """
    return {
        "Parameters": sorted(parameters or PARAMETERS),
        "Sites": list(site_ids),
        "StartDate": start_date.isoformat(),
        "EndDate": end_date_exclusive.isoformat(),
        "Categories": [CATEGORY],
        "SubCategories": [SUB_CATEGORY],
        "Frequency": [FREQUENCY],
    }


def fetch_latest(**kwargs):
    """The latest hour for every site and parameter (empty request body).

    This is fast, and shows which sites report which parameters.
    Returns (records, raw_text).
    """
    records, text = fetch_json("get_Observations", "", **kwargs)
    if not isinstance(records, list):
        raise FetchError("get_Observations (latest) did not return a list")
    return records, text


def fetch_observations(site_ids, start_date, end_date_exclusive, *,
                       sites_by_parameter=None,
                       sites_per_request=SITES_PER_REQUEST, pause_s=1.0,
                       time_budget_s=TIME_BUDGET_S, clock=time.monotonic,
                       sleep=time.sleep, **kwargs):
    """Hourly pollutant averages, requested in small pieces.

    One request for every pollutant at every site is too slow for the NSW
    server (it timed out), so this asks for one pollutant and a batch of
    sites at a time. A piece that still fails after its retries is recorded
    and skipped, so one bad piece doesn't lose the rest of the run.

    If the whole run goes past time_budget_s, the remaining pieces are
    skipped and recorded as failures, so a slow server can't stall the
    scheduled job; the next run's lookback fills them in.

    sites_by_parameter, if given, maps each NSW parameter code to the sites
    that measure it, so empty requests are skipped.

    Returns (records, raw_text, failures), where failures is a list of
    strings describing the pieces that could not be fetched. Raises
    FetchError only if every piece fails.
    """
    site_ids = list(site_ids)
    pieces = []
    for code in sorted(PARAMETERS):
        # Only ask for sites known to measure this pollutant, when known
        ids = site_ids if sites_by_parameter is None else sites_by_parameter.get(code, [])
        pieces.extend((code, ids[i:i + sites_per_request])
                      for i in range(0, len(ids), sites_per_request))

    records, failures = [], []
    run_started = clock()
    for n, (code, batch) in enumerate(pieces, start=1):
        label = f"{code}, sites {batch[0]}-{batch[-1]} ({len(batch)})"
        if clock() - run_started > time_budget_s:
            failures.append(f"{label}: skipped, run exceeded {time_budget_s:.0f}s time budget")
            log.error("Request %d/%d: %s skipped (time budget exceeded)", n, len(pieces), label)
            continue
        started = clock()
        try:
            body = observations_body(batch, start_date, end_date_exclusive, parameters=[code])
            piece, _ = fetch_json("get_Observations", body, sleep=sleep, **kwargs)
            if not isinstance(piece, list):
                raise FetchError("get_Observations did not return a list")
            records.extend(piece)
            log.info("Request %d/%d: %s -> %d records in %.1fs",
                     n, len(pieces), label, len(piece), clock() - started)
        except FetchError as err:
            failures.append(f"{label}: {err}")
            log.error("Request %d/%d: %s failed: %s", n, len(pieces), label, err)
        if n < len(pieces) and pause_s:
            sleep(pause_s)  # be polite to the server between requests

    if pieces and len(failures) == len(pieces):
        raise FetchError(f"all {len(pieces)} observation requests failed; first: {failures[0]}")
    return records, json.dumps(records), failures
