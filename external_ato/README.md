# ATO 2021-22 postcode data cleaning

Turns the official Table 6B into one row per postcode of regional tax features. Keeps the 21-column
metric structure after Claude's revisions, plus a Parquet copy, a validation report, and a runnable
summary. This is retrospective regional-background analysis; it does not perform merchant scoring or
fraud analysis.

## Start here

- `VALIDATION.md`: the actual run's quality results and limitations.
- `curation_summary/ato_summary.ipynb`: the executed analysis write-up, checks, and charts.
- `curation_summary/ato_summary.html`: readable directly in a browser.
- `results/ato_clean.parquet`: recommended as the input for downstream joins; postcode is stored as text.
- `results/ato_clean.csv`, `results/data_dictionary.csv`: the text version of the data and what each column means.
- `median_reference/`: the official Table 8 median/mean candidates, kept separately and not used to replace the core features.

## Running in VS Code

Run from the group repo root; do not run relative commands directly from the outer `project2` directory.

```bash
source external_ato/.venv/bin/activate
python -m pip install -r external_ato/requirements.txt
python external_ato/clean_ato.py --consumers ../project-2-bnpl-tables-part1.zip \
  --compare census=external_census/results/census_clean.csv seifa=external_seifa/results/seifa_clean.csv \
  --output external_ato/results_rerun_new
python -m unittest discover -s external_ato/tests -v
python external_ato/build_summary.py
```

The cleaning output must use an empty directory; the re-run above writes to a new one. The summary
reads from `external_ato/results/` by default and does not automatically switch to a re-run's output.
For a first run, create the venv first with `python3 -m venv external_ato/.venv`. Package dependencies
include notebook and Parquet support. Without `--consumers`, only ATO is cleaned; `--compare` requires
a consumer input to be supplied too.

```python
import pandas as pd
ato = pd.read_parquet("external_ato/results/ato_clean.parquet")
# If you must read the CSV, only fix the postcode dtype:
ato_csv = pd.read_csv("external_ato/results/ato_clean.csv", dtype={"postcode": "string"})
```

## Cleaning rules

1. Only Table 6B is used, not stacked with the 6A sub-groups; 6A is used to reconcile field-by-field,
   and the run stops if a discrepancy exceeds the amount tolerance of 2 AUD (0 for counts) or a key is
   missing.
2. Headers and year are checked, postcode is converted to 4-digit text, and below 0200 is checked
   against the project's valid range. State "other"/overseas aggregate records are audited separately;
   unknown labels, wrong states, and duplicate postcodes raise an error.
3. Missing/invalid counts are kept as null and logged; unknown text symbols raise an error for review.
   Genuine zero values and negative income are kept as-is -- no imputation, winsorising, or removal of
   statistical outliers.
4. Taxable-income and salary derived means each use their own corresponding reporter-count label.
   Salary-recipient share is not an employment rate, and net-tax-payer share is not a risk indicator.
5. A denominator below 100 is only flagged for project review; SA4 state "other" is only a
   source-placeholder flag and is not used to conclude a reason for missing geography.
6. The current version's choice of `total income` field is kept as-is; a highly correlated income
   metric is not treated as independent evidence. This cleaning module does not decide final feature
   weights.
7. Consumer coverage reports both the state breakdown and the combined-key coverage across all three
   sources; the phone-number-range-style classification is only a heuristic and doesn't prove a reason
   for non-matches.

## Official median

Confirmed that Table 8 exists, with 2,270 usable postcodes for the 2021-22 candidate statistics; a
further 47 rows in the table are n/a for that year and are kept as null. The original table notes that
only postcodes with more than 200 returns are published for that year. Table 8's official mean is also
kept separately, without assuming it equals the value derived from Table 6B. Whether to use median or
mean is left to the merchant-features stage; coverage can be compared, but a non-existent official
median cannot be imputed.

```bash
python external_ato/inspect_median.py \
  --xlsx tables/external/ato_2021_22/ts22individual08medianaveragetaxableincomestatepostcode.xlsx \
  --output external_ato/median_reference_rerun
```

## Source and time boundaries

Australian Taxation Office, Taxation statistics 2021-22. CC BY 2.5 Australia (catalogue licence).
Cleaning and derived calculations are this project's own work. The original Excel files are kept in
`tables/external/ato_2021_22/` and are not modified; source metadata and hashes for both sources are
kept in this module.
- Table 6 catalogue: https://data.gov.au/data/dataset/taxation-statistics-postcode-data
- Table 8: https://data.gov.au/data/dataset/4be150cc-8f84-46b8-8c61-55ff1d48a700/resource/9bd9d5af-2c09-405f-b484-69c862f4dc2e

2021-22 is the income year, not an availability date. This version uses returns as processed up to
2023-10-31, and it should not be treated as a predictive feature known at the time a transaction
occurred. ATO postcodes are not identical to ABS POAs; ATO's annual individual income and Census's
weekly household income are also not on the same basis.
The original Excel files are usually excluded by the project's `tables` ignore rules; sharing them
requires also providing the source link or the original file.
