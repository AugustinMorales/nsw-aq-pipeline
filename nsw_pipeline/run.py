"""
One pipeline run: fetch, normalize, validate, store, and log.

Each run requests the last LOOKBACK_HOURS of data, so a missed or failed
run is filled in by the next one, and duplicates are merged away by
storage. Every run, successful or not, adds a row to data/runs.csv.

Usage: python -m nsw_pipeline.run
Exit code is 0 on success and 1 on failure, so a scheduler can flag it.
"""
import gzip
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import client, normalize, storage

LOOKBACK_HOURS = 12  # runs are every 3 hours, so each hour gets about 4 chances
DATA_DIR = Path("data")
RAW_DIR = Path("raw") / "runs"

log = logging.getLogger("nsw_pipeline")


def request_window(now, lookback_hours=LOOKBACK_HOURS):
    """(start_date, end_date_exclusive) in AEST covering the lookback period."""
    now_aest = now.astimezone(normalize.AEST)
    start = (now_aest - timedelta(hours=lookback_hours)).date()
    end_exclusive = now_aest.date() + timedelta(days=1)
    return start, end_exclusive


def save_raw(raw_dir, stamp, name, text):
    """Keep the exact API response (gzipped) for debugging and reprocessing."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{stamp}_{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return path


def run(now=None, data_dir=DATA_DIR, raw_dir=RAW_DIR, lookback_hours=LOOKBACK_HOURS,
        fetch_sites=None, fetch_latest=None, fetch_observations=None):
    """Run the pipeline once and return the run summary.

    The fetch_* arguments let tests replace the API with saved data.
    """
    fetch_sites = fetch_sites or client.fetch_sites
    fetch_latest = fetch_latest or client.fetch_latest
    fetch_observations = fetch_observations or client.fetch_observations
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    summary = {"run_at": normalize.iso_utc(now), "status": "failed", "error": ""}

    try:
        sites, sites_text = fetch_sites()
        save_raw(raw_dir, stamp, "sites", sites_text)
        site_ids = normalize.collectable_site_ids(normalize.build_locations(sites))
        if not site_ids:
            raise RuntimeError("get_SiteDetails returned no usable sites")

        # Find which sites measure which pollutant, to skip empty requests.
        # If this quick lookup fails, fall back to asking every site.
        try:
            latest, _ = fetch_latest()
            by_param = normalize.sites_by_parameter(latest, site_ids)
        except client.FetchError as err:
            log.warning("Could not look up which sites measure what (%s); requesting all sites", err)
            by_param = None
        requested = (len(site_ids) if by_param is None
                     else len(set().union(*by_param.values())) if by_param else 0)

        start, end_exclusive = request_window(now, lookback_hours)
        summary.update(window_start=start.isoformat(),
                       window_end_exclusive=end_exclusive.isoformat(),
                       sites_requested=requested)
        log.info("Requesting %d sites, %s to %s (exclusive)", requested, start, end_exclusive)

        observations, obs_text, failures = fetch_observations(
            site_ids, start, end_exclusive, sites_by_parameter=by_param)
        save_raw(raw_dir, stamp, "observations", obs_text)

        accepted, rejected, skipped = normalize.normalize(observations, sites, now)
        # A blank value for a past hour is not proof the reading is missing:
        # the API sometimes leaves out readings it returned minutes earlier
        # (see SOURCE_NOTES.md). Blanks are counted, never stored over real
        # values, and left for later runs to fill. Only genuinely invalid
        # values go in the rejected log.
        blanks = [r for r in rejected if r["reason"] == "missing_value"]
        rejected = [r for r in rejected if r["reason"] != "missing_value"]
        counts = storage.upsert_measurements(accepted, data_dir)
        storage.upsert_rejected(rejected, data_dir)

        summary.update(records_fetched=len(observations), accepted=len(accepted),
                       rejected=len(rejected), blank=len(blanks), skipped=skipped,
                       failed_requests=len(failures), **counts)
        if not accepted:
            summary["status"] = "no_data"
            summary["error"] = "No valid measurements in the requested window"
        elif failures:
            # Data was stored, but some pieces are missing; the next run's
            # lookback will usually fill them in.
            summary["status"] = "partial"
            summary["error"] = f"{len(failures)} request(s) failed; first: {failures[0]}"[:500]
        else:
            summary["status"] = "success"
        for reason, n in _reason_counts(rejected).items():
            log.info("Rejected %d record(s): %s", n, reason)
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        summary["error"] = "Stopped by user (Ctrl+C)"
        raise
    except Exception as err:  # record any failure in the run log, then report it
        summary["error"] = f"{type(err).__name__}: {err}"[:500]
        log.exception("Run failed")
    finally:
        summary["duration_s"] = round(time.monotonic() - started, 1)
        storage.append_run(summary, data_dir)

    return summary


def _reason_counts(rejected):
    counts = {}
    for r in rejected:
        counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    return counts


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run()
    log.info("Run %s: %s", summary["status"],
             ", ".join(f"{k}={summary[k]}" for k in
                       ("records_fetched", "accepted", "rejected", "blank", "skipped", "failed_requests",
                        "new", "updated", "unchanged", "duration_s") if k in summary))
    if summary["error"]:
        log.error(summary["error"])
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
