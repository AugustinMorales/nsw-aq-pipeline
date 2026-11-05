"""
Monitoring report: how reliably has the pipeline run, and how complete is the data?

Usage: python -m nsw_pipeline.report
"""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import storage

DATA_DIR = Path("data")


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def completeness(measurements):
    """(filled, expected) hourly slots across all station-pollutant series.

    Each series is expected to have a value for every hour from its first to
    its last stored hour, so gaps inside a series count against it.
    """
    series = defaultdict(set)
    for m in measurements:
        series[(m["location_id"], m["parameter"])].add(m["datetime_to"])
    filled = expected = 0
    for hours in series.values():
        times = sorted(_parse(h) for h in hours)
        expected += int((times[-1] - times[0]) / timedelta(hours=1)) + 1
        filled += len(times)
    return filled, expected


def build_report(data_dir=DATA_DIR):
    lines = []
    runs = storage.read_runs(data_dir)
    if not runs:
        return "No runs recorded yet (data/runs.csv is missing or empty)."

    statuses = Counter(r["status"] for r in runs)
    ok = statuses.get("success", 0)
    lines.append("RUNS")
    lines.append(f"  Total runs:     {len(runs)}  ({runs[0]['run_at']} to {runs[-1]['run_at']})")
    lines.append(f"  Succeeded:      {ok} ({ok / len(runs):.0%})")
    for status, n in sorted(statuses.items()):
        if status != "success":
            lines.append(f"  {status + ':':<15} {n}")
    last_ok = [r for r in runs if r["status"] == "success"]
    lines.append(f"  Last success:   {last_ok[-1]['run_at'] if last_ok else 'never'}")
    problems = [r for r in runs if r["status"] != "success"]
    if problems:
        lines.append("  Recent problems:")
        for r in problems[-5:]:
            lines.append(f"    {r['run_at']}  {r['status']}  {r['error']}")

    measurements = storage.read_measurements(data_dir)
    lines.append("")
    lines.append("DATA")
    if not measurements:
        lines.append("  No measurements stored yet.")
        return "\n".join(lines)

    hours = sorted({m["datetime_to"] for m in measurements})
    first, last = _parse(hours[0]), _parse(hours[-1])
    expected = int((last - first) / timedelta(hours=1)) + 1
    present = set(hours)
    missing = [first + timedelta(hours=i) for i in range(expected)
               if (first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") not in present]
    lines.append(f"  Measurements:   {len(measurements):,}")
    lines.append(f"  Locations:      {len({m['location_id'] for m in measurements})}")
    lines.append(f"  Hours covered:  {len(present)} of {expected} ({hours[0]} to {hours[-1]})")
    if missing:
        lines.append(f"  Hours with no data at any site: {len(missing)}"
                     f" (first: {missing[0]:%Y-%m-%dT%H:%M:%SZ})")

    filled, expected_slots = completeness(measurements)
    lines.append(f"  Completeness:   {filled / expected_slots:.1%} of hourly slots filled"
                 f" ({filled:,} of {expected_slots:,}; each station-pollutant series"
                 f" from its first to last stored hour)")

    per_param = Counter(m["parameter"] for m in measurements)
    lines.append("  By pollutant:   " + ", ".join(f"{p} {n:,}" for p, n in sorted(per_param.items())))

    per_day = defaultdict(int)
    for m in measurements:
        per_day[m["datetime_to"][:10]] += 1
    lines.append("  By day (UTC):")
    for day, n in sorted(per_day.items())[-10:]:
        lines.append(f"    {day}  {n:,}")

    rejected_files = sorted((Path(data_dir) / "rejected").glob("*.csv"))
    reasons = Counter()
    for path in rejected_files:
        for row in storage._read(path):
            reasons[row["reason"]] += 1
    lines.append("")
    lines.append("REJECTED RECORDS")
    if reasons:
        for reason, n in reasons.most_common():
            lines.append(f"  {reason:<20} {n:,}")
    else:
        lines.append("  None")
    return "\n".join(lines)


def main():
    print(build_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
