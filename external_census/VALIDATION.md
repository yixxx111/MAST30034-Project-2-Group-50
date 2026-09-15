# Validation of this delivery

Validation date: 15 September 2026. All new artifacts were written under this
Codex task's workspace. Original source data and the supplied Member 2 archive
were read, not edited. No GitHub upload or repository replacement was performed.

## Actual data run

Inputs:

- `2021_GCP_POA_for_AUS_short-header.zip` (the original conversation attachment).
- The local project `tbl_consumer.csv` (pipe-separated, 499,999 data rows).

`results/census_metadata.json` records SHA-256 fingerprints for the original ZIP,
each selected CSV member and the consumer file, as well as the execution versions.
Only postcode was read from the consumer table. No consumer names, addresses or
individual identifiers are included in the deliverable reports.

Observed outputs:

- Five tables, 2,643 rows each; 2 special POA exclusions per table.
- 2,641 ordinary POAs, 41 output columns; unique non-null 4-character postcode.
- 19 selected raw numeric measures: no missing, nonnumeric or nonfinite source
  cells among the accepted POAs.
- Five G02 cells set to null by documented zero-age/household-size policy.
- 178 ratio issue cells: 113 zero denominators, 65 outside [0,1]. Counts retained.
- 68 POAs with at least one unavailable numeric feature, all retained.
- 416,818 of 499,999 consumer rows matched, 83,181 unmatched; no input rows lost.
- 2,640 of 3,167 distinct consumer postcodes matched.
- No statistical outlier removal and no imputation.

The metadata and audit reports, rather than this prose, are the primary evidence
when running the pipeline on new inputs.

## Automated tests

**25 tests passed.** The suite covers:

- postcode padding, whitespace, malformed values and leading zeros;
- exact source schema and invalid/special POA quarantine;
- duplicate POA keys, including duplicates created by normalisation;
- nonnumeric, nonfinite, negative and fractional population counts;
- zero-count preservation and documented zero-median policy;
- employment, unemployment, couple-family and household formulas with known counts;
- zero/missing/out-of-range ratios;
- missing source files and inconsistent source-table POA coverage;
- consumer LEFT JOIN row preservation and distinct non-match causes;
- source-feature missingness versus join-induced missingness;
- refusal of duplicated dimensions and repeated enrichment;
- source immutability, deterministic reruns and Parquet postcode types;
- refusal to overwrite unrelated output folders or source locations;
- preservation of core transaction values and merchant flags in Parquet enrichment;
- transaction-row and dollar-value coverage using known synthetic values;
- Member 2-style `order_year=.../order_month=...` partitioned Parquet;
- clearing stale consumer reports when rerunning without a consumer input.

Run from the repository root:

```bash
python -m pytest external_census/tests -q
```

Tests use temporary synthetic fixtures and do not require the raw project tables.

## Reproducibility check

A second independent run on the actual source ZIP and consumer CSV produced
byte-identical values for all 15 CSV/Parquet output files. Timestamps in metadata
are intentionally run-specific. The original source ZIP and consumer file hashes
still match the recorded fingerprints after execution.

## Notebook and charts

- All 8 code cells executed, with zero notebook cell errors.
- Notebook schema validated with nbformat.
- Both chart outputs were visually inspected; labels and legends are readable.
- The standalone HTML summary was exported from the executed notebook with input
  code hidden. The notebook retains readable code for verification.
- The host emitted a kernel-cleanup permission warning after execution; it did not
  cause cell errors or prevent notebook output from being saved.

## Limits of validation

The actual entire Member 2 transaction dataset was not rerun or enriched. The
Parquet integration path was tested on single-file and partitioned synthetic core
outputs. The actual consumer coverage is **consumer-row weighted**, not transaction
or GMV weighted. Use the optional enrichment command to measure those separately.

This delivery does not claim completion of fraud processing, merchant aggregation,
statistical segmentation, ranking or the full group's course requirements.
