"""
CSV storage with duplicate protection.

Measurements are stored one file per UTC day, keyed by
(location_id, parameter, datetime_to). Because each run re-fetches an
overlapping window, the same hour arrives many times; merging by key means
it is stored once. If NSW later revises a value, the stored row is replaced
and counted as "updated".

All files are written with Unix line endings ("\n") on every computer, so a
run on Windows and a run on GitHub's Linux servers produce identical files
and Git only records rows that actually changed.
"""
import csv
import os
from collections import defaultdict
from pathlib import Path

from .normalize import MEASUREMENT_FIELDS, REJECT_FIELDS

RUN_FIELDS = [
    "run_at", "status", "window_start", "window_end_exclusive",
    "sites_requested", "records_fetched", "accepted", "rejected", "blank", "skipped",
    "failed_requests", "new", "updated", "unchanged", "duration_s", "error",
]


def measurement_key(row):
    return (str(row["location_id"]), str(row["parameter"]), str(row["datetime_to"]))


def reject_key(row):
    return (str(row["source_site_id"]), str(row["source_parameter"]),
            str(row["date"]), str(row["hour"]), str(row["reason"]))


def _read(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path, fields, rows):
    """Write atomically, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _as_strings(row, fields):
    return {f: "" if row.get(f) is None else str(row.get(f)) for f in fields}


def _merge(path, fields, rows, key, compare_field=None):
    """Merge rows into a CSV by key. Returns (new, updated, unchanged)."""
    existing = {key(r): r for r in _read(path)}
    new = updated = unchanged = 0
    for row in rows:
        row = _as_strings(row, fields)
        k = key(row)
        old = existing.get(k)
        if old is None:
            new += 1
            existing[k] = row
        elif compare_field and old.get(compare_field) != row[compare_field]:
            updated += 1
            existing[k] = row
        else:
            unchanged += 1  # keep the original row, including when it was first fetched
    _write(path, fields, sorted(existing.values(), key=key))
    return new, updated, unchanged


def upsert_measurements(measurements, data_dir):
    """Store measurements, one file per UTC day. Returns counts."""
    by_day = defaultdict(list)
    for m in measurements:
        by_day[m["datetime_to"][:10]].append(m)
    totals = {"new": 0, "updated": 0, "unchanged": 0}
    for day, rows in by_day.items():
        path = Path(data_dir) / "measurements" / f"{day}.csv"
        new, updated, unchanged = _merge(path, MEASUREMENT_FIELDS, rows,
                                         measurement_key, compare_field="value")
        totals["new"] += new
        totals["updated"] += updated
        totals["unchanged"] += unchanged
    return totals


def upsert_rejected(rejects, data_dir):
    """Log rejected records, one file per source date, without repeats."""
    by_day = defaultdict(list)
    for r in rejects:
        by_day[str(r.get("date") or "unknown-date")[:10]].append(r)
    for day, rows in by_day.items():
        _merge(Path(data_dir) / "rejected" / f"{day}.csv", REJECT_FIELDS, rows, reject_key)


def append_run(summary, data_dir):
    """Append one row to the run log."""
    path = Path(data_dir) / "runs.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
        if header != RUN_FIELDS:
            # The log was written by an older version with different columns:
            # rewrite it with the current columns so rows stay aligned.
            _write(path, RUN_FIELDS, _read(path))
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_FIELDS, extrasaction="ignore", lineterminator="\n")
        if is_new:
            writer.writeheader()
        writer.writerow(_as_strings(summary, RUN_FIELDS))


def read_runs(data_dir):
    return _read(Path(data_dir) / "runs.csv")


def read_measurements(data_dir):
    rows = []
    for path in sorted((Path(data_dir) / "measurements").glob("*.csv")):
        rows.extend(_read(path))
    return rows
