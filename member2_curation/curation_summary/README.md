# Transaction Curation and Internal Joins

## Deliverable

`src/curation.py` builds a partitioned `curated_transactions` Parquet dataset and its
audit artefacts. It is invoked by the shared command:

```bash
python -m src.run_pipeline --data-root tables --output-root data/curated
```

The source tables are never changed. `tables/` and `data/curated/` are local, Git-ignored
directories because they contain supplied or derived large data.

## Current full-data run

| Check | Result |
| --- | ---: |
| Raw and curated transaction rows | 14,195,505 |
| Quarantined transactions | 0 |
| Unique curated order IDs | 14,195,505 |
| User-to-consumer match rate | 100.00% |
| Merchant master match rate | 95.91% |
| Unmatched merchant transactions | 580,830 across 396 ABNs |
| Transaction period | 2021-02-28 to 2022-10-26 |
| Amount p99 | AUD 1,619.27 |

The unmatched merchant rows are retained with `merchant_master_matched = false`; they are
not silently dropped. The `merchant_match_exceptions.csv` artefact records their value and
time coverage for later follow-up.

## Handoff contract

The curated fact table retains `user_id`, `merchant_abn`, and `order_datetime`, enabling
later enrichment to attach consumer or merchant fraud labels. It also retains normalised
`consumer_postcode` for ABS/SA2 enrichment. Consumer name and address are deliberately
excluded from the curated output.

Run `curation_summary.ipynb` after the pipeline to present the audit, join coverage,
snapshot coverage and merchant-match exception summary.
