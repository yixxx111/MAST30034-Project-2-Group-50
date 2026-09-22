# ATO validation report

Validation date: 2026-09-20. Validated by actually running the current code against the official
source files, not a description of expected results.

## What's been done

- Table 6B input: 2,639 records; output: 2,630 unique postcodes, 21 columns.
- Excluded 8 state-"other" aggregate records and 1 "Overseas" record. Corresponding person counts:
  11,496 and 123,657.
- Total person count across the table: 15,535,395. No ordinary postcode was removed for being a
  statistical outlier, having missing data, or a small denominator.
- 7 selected source fields were reconciled record-by-record between the Table 6A sub-group sums and
  Table 6B; no missing keys, extra keys, or out-of-tolerance records.
- A reconciliation failure now stops the cleaning run, instead of only writing a report.
- The selected numeric and derived fields have no missing values, invalid counts, or out-of-range
  ratios; the quality report keeps the header row.
- 255 postcodes have a derived denominator below 100; 69 postcodes use the state-"other" SA4
  placeholder name. Both are kept and flagged, not removed.
- Before and after this update, the fields and every row value in `ato_clean.csv` are identical,
  preserving Claude's feature revisions.

## CSV and Parquet

Both formats are output together, and the Parquet write/read-back result is automatically checked
for consistency.
Postcode, year, state, SA4 are string; person counts and minimum denominator are int64; quality
flags are bool; amounts/derived values are double.
Synthetic tests cover null-value preservation and the leading zero in `0800`; the summary notebook
checks value consistency between the full CSV and Parquet.
Reading the CSV only needs `dtype={"postcode": "string"}` -- don't convert every numeric column to
string.
Parquet is supported by pandas, PyArrow, DuckDB and Spark; it isn't only intended for Spark.

## Consumer coverage and combined coverage

Consumer postcode/state were read directly from the project's Part 1 ZIP; no name, address or
personal ID is output.
- Total consumers: 499,999; matched to ATO: 414,825 (82.97%); unmatched: 85,174; no invalidly
  formatted postcodes.
- Distinct valid consumer postcodes: 3,167, of which 2,627 matched and 540 did not.
- 889 consumers' recorded state differs from ATO's state; the discrepancy is only logged, not used
  to exclude them.
- Combined ATO+Census+SEIFA postcode coverage: 404,185 people (80.84%). Source comparison hashes are
  kept in the metadata.
- Combined coverage only means the key exists -- it doesn't prove every external field is complete,
  and this is not a transaction-count/amount coverage rate.
- The number-range classification is only a heuristic label; it doesn't prove the postcode type or
  the reason for a non-match, and it cannot be used to conclude that an unmatched postcode was
  necessarily suppressed.

## Official median verification

Downloaded and checked the 2021-22 Individuals Table 8 and its full Notes.
Kept separately in `median_reference/`: 2,317 rows, of which 2,270 have a 2021-22 value and 47 are
officially n/a, kept as null.
The official notes state that from 2013-14 only postcodes with more than 200 returns for that year
are published; from 2016-17, the mean/median uses individuals reporting a taxable income or loss
label (including zero values).
The candidate table is not merged into the 21-column core output, and no unilateral decision is made
about using mean vs. median.
Coverage and statistical basis need to be compared at the feature-selection stage; the median cannot
be inferred from Table 6B's totals.
Source and original-file SHA-256 hashes are recorded in `median_reference/metadata.json`; the
original Notes text is also kept.

## Automated tests

Run: `python -m unittest discover -s external_ato/tests -v`
Actual result: 16 passed (including the official-Excel regression test, none skipped).
Coverage: postcode normalisation, unknown labels/states, duplicate keys, missing fields, negative
income preserved, missing/zero denominators, out-of-range ratios, small denominators, SA4
placeholders, CSV/ZIP consumer reading, combined coverage, 6A/6B reconciliation and its stop
condition, and Parquet types/nulls.

## Running the summary

Run: `python external_ato/build_summary.py`
The notebook includes shape checks, CSV/Parquet consistency, reconciliation, coverage, the data
dictionary, two charts, and notes on the official median candidates.
`curation_summary/ato_summary.ipynb` keeps the executed results; the HTML gives a readable version
that doesn't need a notebook environment.

## Scope of use

2021-22 is the income reference year; the source data is processed as at 2023-10-31 and cannot be
used to claim it was known as a historical prediction at the time of 2021-22 itself.
Regional tax values are not an individual's actual income, disposable income, purchasing power, or
credit risk.
Full transaction integration, merchant-level feature construction, fraud analysis, and merchant
scoring have not been done here; they are out of scope for this cleaning update.

## Backups

A sibling directory to the group repo, `ato_backups_20260920/`, keeps the original Codex 2026-09-16
ZIP, Claude's modified ZIP, and the results from before this update. The original was restored from
a still-existing `/private/tmp/ato-work` copy and has not overwritten the current code.
