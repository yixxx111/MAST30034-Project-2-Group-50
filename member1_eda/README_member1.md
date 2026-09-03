# Member 1 EDA — Submission Summary

## Scope

This deliverable completes Member 1's Sprint 1 data validation and exploratory analysis using only the supplied project data. Fraud probability tables are catalogued but not analysed, and no ABS/external data is used.

## Main results

- Transactions: **14,195,505**
- Consumers: **499,999**
- Merchants: **4,026**
- Missing cells across the four core tables: **0**
- Duplicate `order_id` rows beyond the first: **0**
- Transactions with unknown users: **0**
- Transactions with unknown merchants: **580,830**
- Distinct unmatched merchant ABNs: **396**
- Transaction period: **2021-02-28 to 2022-10-26**
- Missing calendar dates inside the period: **0**
- Peak day: **2021-11-26 (61,237 transactions)**
- Change in average daily transactions, last 28 days versus first 28 days: **+61.1%**
- Mean / median transaction value: **$166.23 / $62.23**
- 99th percentile / maximum: **$1,619.27 / $105,193.89**
- Merchant-tag parse success: **100.00%**

## Important data-quality observations

1. Transaction amounts are right-skewed. The histogram is capped at the 99th percentile for visibility; no records were removed.
2. Merchant tags use inconsistent case and bracket styles. A derived, normalised category is used for EDA while raw tags remain intact.
3. Postcodes should be stored as zero-padded four-character strings.
4. `transactions_20220228_20220828_snapshot` contains partitions through **2022-10-26**, beyond the date in its folder name. ETL logic should rely on the partition value and record this source inconsistency.

## Files

- `member1_eda.ipynb`: complete, executed notebook.
- `member1_eda_outputs/figures/`: three submission-ready figures.
- `member1_eda_outputs/*.csv`: schema, missingness, duplicate-key, relationship and trend summaries.

To rerun, place the notebook beside the `tables` folder, or set the `PROJECT2_DATA_ROOT` environment variable to the extracted `tables` directory.
