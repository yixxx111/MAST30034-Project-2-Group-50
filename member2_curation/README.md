# MAST30034 Project 2

This repository contains a reproducible Buy Now, Pay Later merchant-ranking pipeline.
The transaction curation module cleans and joins the supplied internal tables for downstream analysis.

## Data setup

Extract the supplied data so the following files are below a local `tables/` directory:

- `tbl_consumer.csv`
- `consumer_user_details.parquet`
- `tbl_merchants.parquet`
- all `transactions_*_snapshot/order_datetime=*/part-*.parquet` files

`tables/` is intentionally ignored by Git because it contains the supplied large data.
Either place it at the repository root or set `PROJECT2_DATA_ROOT` to its absolute path.

## Installation and run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.run_pipeline --data-root tables --output-root data/curated
pytest
```

The pipeline creates `data/curated/curated_transactions/` as partitioned Parquet, plus
the audit, quarantine, join-coverage, snapshot-coverage, and merchant-exception files.

## Shared data contract

`curated_transactions` retains the transaction keys, date, amount, `user_id`,
`consumer_id`, `merchant_abn`, privacy-minimised consumer demographics, merchant name/tags,
source snapshot, merchant-match flag, and p99 amount-outlier flag. It excludes consumer name
and address. Later enrichment can attach fraud labels using `user_id + order_datetime` or
`merchant_abn + order_datetime`, and ABS features using `consumer_postcode`.

## Project layout

- `src/`: shared automated ingestion and curation code.
- `tests/`: synthetic data-quality and join-contract tests.
- `curation_summary/`: concise notebook and documentation for curation results.
