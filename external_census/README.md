# ABS Census 2021 POA curation

A separate external-data stage for MAST30034 Project 2. It follows the supplied
`member2_curation` pattern: reusable cleaning logic, command-line entry point,
quality reports, tests and a readable summary notebook. It does **not** replace
Member 2, change its transaction rules or modify original input files.

## Start here

1. Open `curation_summary/census_summary.ipynb` for the executed results.
2. Use `results/census_clean.parquet` as the typed postcode dimension.
   `results/census_clean.csv` is the same data in a convenient text format.
3. Use `results/data_dictionary.csv` for every output column and formula.

The supplied results are an actual run on the uploaded ABS ZIP and the local
`tbl_consumer.csv`. They are not synthetic demonstration results.

## Verified result

- Five source CSVs, each with 2,643 rows and no ordinary duplicate POA keys.
- Two special geographies excluded from the postcode dimension: `POA9494`
  (No Usual Address) and `POA9797` (Migratory / Offshore / Shipping).
- **2,641 rows, 41 columns** in the clean dimension: 2 keys, 19 source measures,
  1 year field, 1 combined family count, 10 proportions and 8 review flags.
- **416,818 / 499,999 consumer rows matched (83.36%)**. The remaining **83,181**
  have syntactically valid postcodes absent from this POA dataset.
- **2,640 / 3,167 distinct observed postcodes matched**; 527 did not.
- No consumer rows were removed. No missing values were imputed.
- The actual full transaction dataset has **not** been enriched in this delivery.
  The optional Parquet enrichment stage is provided and tested with synthetic
  transaction fixtures. Consumer coverage is not transaction-weighted coverage.

## Repository placement

Put the **whole `external_census/` folder next to** `member2_curation/`:

```text
project-root/
├── member2_curation/           # existing module, unchanged
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

Commands below run from **project-root**, not from `member2_curation`.
Relative imports prevent the two modules' `src/` packages from conflicting.

## Install and run

Use a separate virtual environment if Member 2's pinned dependencies differ.
Do not replace its requirements file.

```bash
python3 -m venv external_census/.venv
source external_census/.venv/bin/activate
python -m pip install -r external_census/requirements.txt
python -m external_census.src.run_pipeline \
  --zip tables/external/2021_GCP_POA_for_AUS_short-header.zip \
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

## Optional: add Census to Member 2 transactions

After Member 2 has produced `curated_transactions`, run:

```bash
python -m external_census.src.enrich_transactions \
  --transactions member2_curation/data/curated/curated_transactions \
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
requires a unique non-null `order_id`, as Member 2 promises, and does not remove
unmatched transactions. `merchant_master_matched`, dates, amounts and other
existing core fields are retained. Existing `census_` columns cause an error,
preventing accidental double enrichment. The original Member 2 quality profile
still describes its original base stage; use the new enrichment report for the
Census stage. Fraud processing remains a separate group responsibility.

## Selected source fields

All files also include `POA_CODE_2021`.

| Table / exact CSV | Selected short headers |
|---|---|
| `2021Census_G01_AUST_POA.csv` | `Tot_P_P` |
| `2021Census_G02_AUST_POA.csv` | `Median_age_persons`, `Median_tot_hhd_inc_weekly`, `Average_household_size` |
| `2021Census_G29_AUST_POA.csv` | `CF_no_children_F`, `CF_Total_F`, `OPF_Total_F`, `Other_family_F`, `Total_F` |
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
   follows Member 2: trim 1-4 digits, pad to four. Decimal text such as `3000.0`
   is rejected rather than guessed. POA formatting and actual postal validity
   are separate concepts.
4. Invalid/nonfinite numeric values become null with cell-level evidence, without
   deleting the POA. Counts must be nonnegative integers. There were no missing,
   nonnumeric or nonfinite source cells in the 19 selected measures for ordinary
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
   counts remain available. Ratios are proportions, not percentage values.
7. ABS confidentiality perturbation means totals need not add up exactly. Report
   differences; do not force equality. Low denominators below 30 are review flags
   only: this is a project threshold, not an ABS reliability certification.
8. Flag upper 1% for population, income, age and household size for inspection.
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

## Course requirement mapping

Based on the actual local **MAST30034_Project_2_Buy_Now_Pay_Later_Spec.pdf**:

| Requirement | Evidence in this module |
|---|---|
| Python automated ingestion and business rules, p.1 | `src/curation.py`, `src/run_pipeline.py` |
| External dataset selection and benefit, p.2 | Census feature rationale and dictionary |
| Sprint 2: NULLs after joins, how many, treatment, p.3 | consumer/transaction coverage and feature-missingness reports |
| Sprint 2: outlier treatment and distributions, p.3 | outlier flags, summary and Notebook histograms; no outliers omitted |
| Sprint 3: automated end-to-end ETL, p.3 | command-line workflow and tested optional Parquet stage |
| Readable Notebook, assumptions and reproducible code, pp.2,4 | executed Notebook, README, provenance and tests |

The specification recommends SA2; this implementation uses POA because the internal
consumer key is postcode. Document that geographic choice with your tutor. It is not
an SA2 dataset or a fabricated postcode-to-SA2 correspondence. Geospatial visuals
are recommended in the specification, but are not included here because this ZIP
contains no boundary geometry. This is the Census curation component, not completion
of the group's fraud, segmentation, modelling or final merchant ranking requirements.

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

Member 2 compatibility was checked against the supplied `project2_submit(1).zip`,
including `src/curation.py`, `src/run_pipeline.py`, `src/data_quality.py` and README.

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
