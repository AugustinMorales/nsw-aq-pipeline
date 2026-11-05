# Monitoring log

The pipeline runs every 3 hours on GitHub Actions. This file records the month-long monitoring period: what ran, what failed, and what was fixed.

## How to check on the pipeline

1. **Actions tab on GitHub.** Each run of "Collect NSW air quality data" shows a green check or a red cross. GitHub emails you when a scheduled run fails.
2. **Run log.** `data/runs.csv` has one row per run: status, records fetched, accepted, rejected, new, updated, duration, and any error.
3. **Report.** Pull the latest data and print a summary:

   ```
   git pull
   python -m nsw_pipeline.report
   ```

   It shows the run success rate, recent problems, hours covered, hours with no data, counts by pollutant and day, and rejected records by reason.

4. **Raw responses.** Each workflow run keeps the exact API responses as a downloadable artifact for 90 days, for investigating anything odd.

## Monitoring period

- Start date:
- End date:

## Weekly check-ins

Run the report once a week and record the key numbers.

| Week ending | Runs | Success rate | Hours covered | Measurements | Rejected | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
| | | | | | | |
| | | | | | | |
| | | | | | | |

## Incidents and fixes

Record each failure or data problem: what happened, the cause, and what was changed.

| Date | What happened | Cause | Fix |
| --- | --- | --- | --- |
| | | | |

## Summary at the end of the month

- Total runs and success rate:
- Total measurements collected:
- Gaps in the data and why:
- Changes made during the month:
- What I would improve next:
