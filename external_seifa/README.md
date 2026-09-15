# ABS SEIFA 2021 Postal Area curation

This folder provides a reproducible external-data stage for MAST30034 Project 2. It downloads no data during execution and never changes the raw workbook. The supplied run used the official ABS **Postal Area, Indexes, SEIFA 2021.xlsx** file.

## Start here

1. Open `curation_summary/seifa_summary.ipynb` for the executed summary.
2. Use `results/seifa_clean.parquet` as the typed postcode dimension. `results/seifa_clean.csv` contains the same rows in text form.
3. Read `results/data_dictionary.csv` before using the fields.
4. Use `results/consumer_join_coverage.csv` and `results/consumer_postcode_exceptions.csv` to report postcode coverage.

## Verified output

- 2,643 published POA records were reconciled across the summary, four index tables and the excluded-area table.
- `POA9494` (No Usual Address) and `POA9797` (Migratory / Offshore / Shipping) were kept in audit outputs but excluded from the join dimension.
- The clean dimension contains **2,641 ordinary POAs and 59 columns**.
- **2,624 POAs have all four scores**, 3 have only IEO, and 14 have no published index score. Ordinary POAs with missing scores are retained and are not treated as failed postcode joins.
- The four detailed index tables contain 2,624 IRSD, IRSAD and IER rows, and 2,627 IEO rows.
- No official score was imputed, trimmed, winsorised or removed as an outlier.
- The summary-table score and decile values agree with all four detail tables.
- Consumer audit: 416,818 of 499,999 rows match the SEIFA geography. Among all consumer rows, 414,125 have all four scores, 472 have partial scores and 2,221 match a POA with no score.
- The transaction dataset has not been enriched in this saved delivery. Optional code is provided.

## Source

- Data page: https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/2021
- Direct workbook: https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/2021/Postal%20Area%2C%20Indexes%2C%20SEIFA%202021.xlsx
- Methodology: https://www.abs.gov.au/methodologies/socio-economic-indexes-areas-seifa-australia-methodology/2021

The raw workbook is stored locally at:

```text
tables/external/abs_seifa_2021/Postal Area, Indexes, SEIFA 2021.xlsx
```

Its SHA-256 hash for this run is recorded in `results/seifa_metadata.json`.

## Install and run

From the project root:

```powershell
python -m pip install -r external_seifa/requirements.txt
python -m external_seifa.src.run_pipeline `
  --xlsx "tables/external/abs_seifa_2021/Postal Area, Indexes, SEIFA 2021.xlsx" `
  --consumer-csv "tables/tbl_consumer.csv" `
  --output-root "external_seifa/results"
python -m pytest external_seifa/tests -q
python -m external_seifa.src.build_notebook
```

The consumer reader imports only the postcode column. Consumer names, addresses and identifiers are not written into the audit results.

## Cleaning rules

1. Read the summary table, four detailed index tables and the official excluded-area table using the fixed header row and schema.
2. Preserve POA codes as four-character text so leading zeros such as `0800` remain intact.
3. Reject duplicate POA keys after normalisation.
4. Reconcile the summary and detailed score/decile values; fail instead of silently accepting mismatches.
5. Exclude the two non-ordinary special geographies from the join dimension, while preserving them in audit output.
6. Retain ordinary POAs without scores. A matched postcode with an unavailable score is different from an unmatched postcode.
7. Keep scores, ranks, deciles, percentiles, population coverage and ABS caution flags. Validate numeric ranges and record issues.
8. Add national percentile divided by 100 as the only convenience scaling. It is deterministic and keeps the official ordering.
9. Do not impute missing scores and do not remove outliers.
10. Join consumers or transactions using a many-to-one LEFT JOIN so the source row count cannot decrease.

## Four indexes

- **IRSD** measures relative socio-economic disadvantage. A lower value indicates greater disadvantage.
- **IRSAD** measures both relative advantage and disadvantage. A higher value indicates more advantage and less disadvantage.
- **IER** focuses on access to economic resources, including income and housing-related variables.
- **IEO** focuses on education and occupation.

The same area may rank differently across the four indexes. ABS recommends rankings or quantiles for most analysis. Scores are ordinal on an arbitrary standardised scale; a score of 1,000 is not twice a score of 500.

## Optional transaction enrichment

After Member 2 creates `curated_transactions`, run:

```powershell
python -m external_seifa.src.enrich_transactions `
  --transactions "member2_curation/data/curated/curated_transactions" `
  --seifa-parquet "external_seifa/results/seifa_clean.parquet" `
  --output-root "data/curated/seifa_enriched"
```

This writes a new `curated_transactions_with_seifa.parquet` plus join coverage, feature missingness, postcode exceptions and metadata. It leaves Member 2's base table unchanged. The join key is normalised `consumer_postcode`; merchant ABN is not involved.

## Modelling cautions

- SEIFA describes an area and must not be presented as an individual consumer or merchant attribute.
- POA is a Census statistical approximation to postal areas and not every delivery postcode is represented.
- State rankings are unavailable for Cross Border and Other Territories POAs.
- The four indexes share underlying Census concepts. Averaging all four can double-count related information.
- SEIFA 2021 is a point-in-time area measure, not a GDP trend. Do not use it to infer change from 2020 to 2021.
- Choose model weights only during downstream modelling and validation. This curation stage does not assign merchant risk or ranking weights.

## Files

- `src/curation.py`: workbook reading, validation, feature creation and consumer coverage.
- `src/enrich_transactions.py`: optional scalable Parquet LEFT JOIN using DuckDB.
- `src/build_notebook.py`: executed summary notebook and HTML export.
- `tests/test_curation.py`: synthetic schema, missing-score, join and row-preservation tests.
- `results/`: clean dimension and audit reports.
- `VALIDATION.md`: actual run checks and limitations.

## Attribution

Based on Australian Bureau of Statistics data: *Socio-Economic Indexes for Areas (SEIFA), Australia, 2021*, released 27 April 2023, accessed 15 September 2026. ABS data used with permission from the Australian Bureau of Statistics. Derived scaling, cleaning policies, audit reports and charts are project work.
