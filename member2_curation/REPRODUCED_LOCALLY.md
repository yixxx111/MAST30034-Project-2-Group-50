# Locally reproduced (2026-09-20)

`data/curated/curated_transactions/`, `quarantined_transactions.parquet`, and
`curation_audit.csv`, `join_coverage.csv`, `snapshot_coverage.csv`, `merchant_match_exceptions.csv`,
`data_quality_profile.csv`, `curation_metadata.json` were regenerated on this machine by re-running
`src/run_pipeline.py` (the cleaning code itself is unmodified) -- they are not the original author's
own copy of these files.

## Why it was re-run

`curated_transactions` (14M+ transaction rows) is too large and was never transferred from the
original author; only the audit/summary CSVs existed on GitHub, not the data itself. It turned out
the raw transaction snapshot data (`transactions_*_snapshot/`) was the same dataset released to the
whole class on Canvas ("Dataset Release"), not something unique to the original author, so it could
be downloaded directly and combined with the cleaning code already committed to the repo to
independently reproduce the same `curated_transactions` locally, without waiting on or depending on
the original author's machine.

## Reproduction check

The key numbers produced here match the original author's `RUN_RESULTS.md` / `RUN_VERIFICATION.json`
exactly:

| Metric | Original author | Reproduced locally |
|---|---|---|
| Input/output transaction count | 14,195,505 | 14,195,505 |
| Unique order_id count | 14,195,505 | 14,195,505 |
| Quarantined rows | 0 | 0 |
| Merchant-unmatched transactions | 580,830 | 580,830 |
| Amount p99 | 1619.2727559488073 | 1619.2727559488073 |
| Automated tests | 4 passed | 4 passed |

`join_coverage.csv` matches byte-for-byte; `merchant_match_exceptions.csv`'s 397 rows match exactly
(the amount field has a negligible rounding difference in the last digit from floating-point
summation order, which is expected and doesn't affect the values themselves).

## The data itself is not in the repo

`curated_transactions/` (~790MB) and `quarantined_transactions.parquet` have been added to the root
`.gitignore` and are not committed to GitHub, consistent with how the original author always handled
this data.
