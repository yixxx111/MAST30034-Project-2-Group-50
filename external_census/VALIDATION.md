# Validation

Validated on 15 September 2026. This delivery is based on `external_census.zip`;
its original curation, audit, consumer-coverage and optional transaction-enrichment
paths remain in place. The source ZIP is read directly and never modified.

## Actual Census run

The pipeline was run against the official ABS 2021 General Community Profile
Postal Areas DataPack:

- ZIP: `2021_GCP_POA_for_AUS_short-header.zip`
- source URL: recorded in `results/census_metadata.json`
- ZIP SHA-256:
  `ccca4c72ee769d81c13dd6cfaedff7df58819267384195118f3d3b87d6c64008`
- seven source CSVs, each with 2,643 rows;
- `POA9494` and `POA9797` excluded from the join dimension;
- 2,641 ordinary POAs and 59 curated columns;
- seven source-table audits, five source-cell issues, 191 undefined or
  out-of-range derived-ratio records, and 7,004 non-additivity records retained
  for review; and
- no source rows, outliers or missing values were imputed or silently deleted.

The updated run also produces `published_rate_comparison.csv`: 5,282 rows covering
two published G43 percentages for every ordinary POA. It makes any difference from
G46B count-derived rates explicit rather than treating the two measures as identical.

## Consumer coverage rerun — 16 September 2026

The pipeline was rerun with the supplied `tbl_consumer.csv`: 416,818 of 499,999
consumer records matched (83.36%); 83,181 remained unmatched (16.64%).
All four consumer audit reports were generated. No consumer records were removed
or imputed. The full transaction enrichment stage was not run.

The summary notebook cells were executed sequentially in an in-process IPython
session and outputs saved; an HTML summary was exported. Cleaning source code
is unchanged. All 25 existing tests passed again. Runtime versions are recorded
in `results/census_metadata.json`.

## Automated tests

```bash
python -m pytest external_census/tests -q
```

The updated suite completed successfully: **25 passed**. It covers source schemas,
malformed and special POAs, duplicate keys, numeric-cell validation, source-table
coverage, the added age/income/degree formulas, published-rate comparison, bounded
ratios, output replacement safety, consumer LEFT JOIN row preservation and
single-file/partitioned Parquet transaction enrichment.

## Reproduce

From the project root, either point to a previously downloaded DataPack or let the
CLI fetch the official file:

```bash
python -m external_census.src.run_pipeline \
  --zip tables/external/2021_GCP_POA_for_AUS_short-header.zip --download \
  --output-root external_census/results
```

Each run records the source URL, ZIP size and hash, selected member hashes, software
versions, output shape and policies in `results/census_metadata.json`.

## Scope limits

POAs are an ABS approximation to postcodes, not an exact delivery-postcode
crosswalk. Census values are 2021 area context, not individual consumer attributes;
they should not be summed once per transaction or interpreted causally. The optional
enrichment path is deliberately a many-to-one LEFT JOIN so that unmatched postcodes
remain visible as missing rather than being converted to zero.
