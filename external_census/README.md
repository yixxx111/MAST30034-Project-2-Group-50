# ABS Census 2021 POA curation

An external-data stage for MAST30034 Project 2: reusable cleaning logic,
a command-line entry point, quality reports, tests and a readable summary notebook.
It leaves original source files and the curated transaction fact table unchanged.

## Start here

1. Open `curation_summary/census_summary.ipynb` for the executed results.
2. Use `results/census_clean.parquet` as the typed postcode dimension.
   `results/census_clean.csv` is the same data in a convenient text format.
3. Use `results/data_dictionary.csv` for every output column and formula.

The supplied Census result is an actual run on the official ABS DataPack. This
16 September 2026 rerun includes actual consumer coverage: 416,818 / 499,999
consumers matched (83.36%). All four consumer audit reports are included.
Full transaction enrichment has not been run.

## Verified result

- Seven source CSVs, each with 2,643 rows and no ordinary duplicate POA keys.
- Two special geographies excluded from the postcode dimension: `POA9494`
  (No Usual Address) and `POA9797` (Migratory / Offshore / Shipping).
- **2,641 rows and 59 columns** in the clean dimension, including the original
  demographics, household structure and labour features plus age 20–44,
  high-income-household and bachelor-degree features.
- `published_rate_comparison.csv` compares ABS-published G43 unemployment and
  labour-force-participation percentages with rates derived from G46B counts.
  The published percentages are the recommended analysis features; derived rates
  are a QA check only.
- No POA, consumer or transaction rows are removed by a join. No missing values
  are imputed and no statistical outliers are removed.
- The actual full transaction dataset has **not** been enriched in this delivery.
  The optional Parquet enrichment stage is provided and tested with synthetic
  transaction fixtures. Consumer coverage is not transaction-weighted coverage.

## Repository placement

Put the **whole `external_census/` folder next to** the shared curation project:

```text
project-root/
├── curation_pipeline/          # shared transaction curation, unchanged
├── external_census/            # this delivery
│   ├── src/
│   ├── tests/
│   ├── curation_summary/
│   ├── results/
│   ├── README.md
│   └── requirements.txt
└── tables/
    ├── tbl_consumer.csv
    └── external/
        └── 2021_GCP_POA_for_AUS_short-header.zip
```

Commands below run from **project-root**. Relative imports prevent package-name
conflicts with the shared curation module.

## Install and run

Use a separate virtual environment if the shared transaction pipeline pins different
dependencies. Do not replace its requirements file.

```bash
python3 -m venv external_census/.venv
source external_census/.venv/bin/activate
python -m pip install -r external_census/requirements.txt
python -m external_census.src.run_pipeline \
  --zip tables/external/2021_GCP_POA_for_AUS_short-header.zip --download \
  --consumer-csv tables/tbl_consumer.csv \
  --output-root external_census/results
python -m pytest external_census/tests -q
```

The ZIP is read directly, without manual extraction. The supplied consumer CSV
is pipe-delimited; the reader detects pipe versus comma and imports only the
postcode column. Consumer names, addresses and IDs are never included in these
summary outputs. `--consumer-csv` is optional; without it, coverage is explicitly
marked `not_run_no_consumer_input`, not zero or 100%.

`--output-root` must be empty or a dedicated prior Census output directory.
A rerun replaces that directory after computation succeeds; do not put unrelated
files in it. Raw inputs may not be inside this directory. Source hashes and
software versions are recorded in `census_metadata.json`.

## Optional: append Census to curated transactions

After the shared pipeline has produced `curated_transactions`, run:

```bash
python -m external_census.src.enrich_transactions \
  --transactions curation_pipeline/data/curated/curated_transactions \
  --census-parquet external_census/results/census_clean.parquet \
  --output-root data/curated/census_enriched
```

Adjust the transaction path to the location actually used by your group. Both a
Parquet file and a partitioned directory are supported. This writes a **new**
`curated_transactions_with_census.parquet`, plus:

- `transaction_join_coverage.csv`: row coverage and, when `dollar_value` exists,
  transaction-value coverage.
- `transaction_feature_missingness.csv`: all versus matched-row missing cells.
- `transaction_postcode_exceptions.csv`: unmatched postcode counts.
- `enrichment_metadata.json`: input/output row counts and source identity.

For one invocation from raw Census to enriched transactions, add both
`--curated-transactions PATH` and `--enriched-output-root PATH` to the first
`run_pipeline` command. The Census stage and transaction stage each have separate
metadata. If the transaction stage fails, the completed Census stage remains
available; the command fails rather than reporting full success.

The join is on normalised `consumer_postcode`, many-to-one and LEFT JOIN. It
requires a unique non-null `order_id` and does not remove
unmatched transactions. `merchant_master_matched`, dates, amounts and other
existing core fields are retained. Existing `census_` columns cause an error,
preventing accidental double enrichment. The original transaction quality profile
still describes its base stage; use the new enrichment report for the Census stage.

## Selected source fields

All files also include `POA_CODE_2021`.

| Table / exact CSV | Selected short headers |
|---|---|
| `2021Census_G01_AUST_POA.csv` | `Tot_P_P`, `Age_20_24_yr_P`, `Age_25_34_yr_P`, `Age_35_44_yr_P` |
| `2021Census_G02_AUST_POA.csv` | `Median_age_persons`, `Median_tot_hhd_inc_weekly`, `Average_household_size` |
| `2021Census_G29_AUST_POA.csv` | `CF_no_children_F`, `CF_Total_F`, `OPF_Total_F`, `Other_family_F`, `Total_F` |
| `2021Census_G33_AUST_POA.csv` | `Tot_Tot`, `HI_3000_3499_Tot`, `HI_3500_3999_Tot`, `HI_4000_more_Tot` |
| `2021Census_G43_AUST_POA.csv` | `P_15_yrs_over_P`, `Percent_Unem_loyment_P`, `Percnt_LabForc_prticipation_P`, `non_sch_qual_Bchelr_Degree_P` |
| `2021Census_G46B_AUST_POA.csv` | `P_Tot_Emp_Tot`, `P_Tot_Unemp_Tot`, `P_Tot_LF_Tot`, `P_Not_in_LF_Tot`, `P_LFS_NS_Tot`, `P_Tot_Tot` |
| `2021Census_G42_AUST_POA.csv` | `Tot_FHs_Tot`, `Tot_Lone_P_H`, `Tot_Group_H`, `Tot_Tot` |

**G29 `CF_Total_F` means couples WITH children**, not all couples. Its `_F` means
families, not females. Total couple families therefore equals
`CF_no_children_F + CF_Total_F`. Families and households have different denominators.

## Cleaning decisions and why

1. Require exact schema and one unambiguous ZIP member per table. Fail on duplicate
   CSV headers, duplicate ordinary POA keys or different accepted key coverage
   between tables. No arbitrary deduplication or silent inner-join losses.
2. Normalise POA to uppercase `POA` plus 4 ASCII digits. Quarantine malformed keys
   and the two special geographies in `excluded_poa_records.csv`.
3. Preserve postcode as text, e.g. `POA0800` becomes `0800`. Consumer normalisation
   follows the shared transaction-curation contract: trim 1-4 digits, pad to four. Decimal text such as `3000.0`
   is rejected rather than guessed. POA formatting and actual postal validity
   are separate concepts.
4. Invalid/nonfinite numeric values become null with cell-level evidence, without
   deleting the POA. Counts must be nonnegative integers. There were no missing,
   nonnumeric or nonfinite source cells in the 30 selected measures for ordinary
   POAs in this run.
5. Zero counts remain zero. A zero median age or nonpositive average household
   size is left analytically unavailable and logged: this is a conservative
   **project policy**, not a claim that ABS defines every zero as missing.
   This changed five G02 cells across four POAs. Zero/negative household income
   is retained with a review flag; it is not automatically interpreted as missing
   or proof of no purchasing power.
6. Ratios use the correct population: employed / persons 15+, unemployed / labour
   force, family counts / total families, household counts / total households.
   Persons 15+ includes labour-status-not-stated persons. A denominator of zero
   yields null. A ratio outside [0,1] also yields null and a report, while the source
   counts remain available. Derived ratios are proportions. G43's unemployment and
   labour-force-participation values are published percentage points and are
   compared against their G46B-derived counterparts in a separate QA report.
7. ABS confidentiality perturbation means totals need not add up exactly. Report
   differences; do not force equality. Low denominators below 30 are review flags
   only: this is a project threshold, not an ABS reliability certification.
8. Flag upper 1% for population, income, age, household size and the added count
   features for inspection.
   Remove no statistical outliers, perform no winsorisation, and do no imputation.
   Model transformations should be decided later and fitted on training data.

## Missingness after joining

Three cases are kept distinct:

- `missing_or_invalid_postcode`: input key is missing or malformed.
- `postcode_not_in_census` / `special_geography`: no ordinary POA match; all
  Census features remain null, `census_matched=False`.
- `matched` but a feature is null: Census input or a ratio is unavailable; this
  is **not** a postcode-join failure. See numeric and ratio issue reports.

Do not replace unmatched records with zero income or zero population. The supplied
consumer data are synthetic; non-match causes cannot be assigned to specific postal
categories from the postcode string alone. POA excludes some postcodes, including
non-street-delivery codes, but the report does not claim that explains every non-match.

## Interpretation limits

- Population, age and employment are area context, not individual consumer facts.
- POA approximates postcodes. Postal boundaries and delivery codes are not exact
  Census geographic boundaries.
- G01/G46 use usual residence; G29/G42 use place of enumeration. G42 excludes
  visitors-only/other non-classifiable households and uses the primary family for
  multiple-family household composition.
- G02 household median income excludes households with unstated adult income or
  temporarily absent adult members. Median income is AUD/week, not annual income,
  disposable income or personal income.
- 2021 values are a cross-sectional contextual snapshot. Do not claim them as
  current values, or use them as historically available predictors before their
  release. Geography matching does not resolve temporal leakage.
- Do not sum Census populations once per transaction: it would multiply the same
  area repeatedly. Downstream merchant summaries must define customer/area weights
  explicitly and report Census coverage alongside each feature.

## Sources inspected

Within the original ABS ZIP:
`Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx`,
`Metadata/2021_GCP_Sequential_Template_R2.xlsx`,
`Metadata/2021Census_geog_desc_1st_2nd_3rd_release.xlsx`,
`Readme/2021POA_readme.txt`, and `Readme/2021AboutDataPacks_readme.txt`.
The exact ZIP and selected member hashes are saved with each run.

The postcode, order ID and transaction-output conventions were checked against the
supplied shared curation bundle.

## Files

- `src/curation.py`: source reading, validation, feature creation and consumer audit.
- `src/data_quality.py`: safe ratios, distributions and outlier flags.
- `src/enrich_transactions.py`: optional scalable Parquet LEFT JOIN via DuckDB.
- `tests/test_curation.py`: synthetic boundary, formula, join and rerun tests.
- `curation_summary/census_summary.ipynb`: executed, rerunnable results narrative.
- `results/`: real Census output and aggregate consumer audits; no personal details.
- `VALIDATION.md`: test scope, actual execution and limitations.

## Attribution

Based on Australian Bureau of Statistics data: 2021 Census of Population and Housing,
General Community Profile DataPack, Postal Areas, first/second release; supplied ZIP
accessed 15 September 2026. ABS data used with permission from the Australian Bureau
of Statistics. See [ABS](https://www.abs.gov.au/) and `SOURCE_LICENCE.txt` (CC BY 4.0).
Derived ratios, cleaning policies and charts are project work. Synthetic consumer
data and consumer match coverage are not attributed to the ABS.
