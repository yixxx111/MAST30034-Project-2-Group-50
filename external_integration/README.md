# External data integration (Census + SEIFA + ATO)

Merges the three teammates' already-cleaned postcode-level external datasets (Census, SEIFA, ATO)
into a single postcode-level feature dimension table by postcode, then uses it to join onto consumer
and transaction data. The goal is to give the later merchant-feature-construction step regional
population/income/socio-economic background -- this module does not do merchant scoring or fraud
analysis.

## Start here

- `VALIDATION.md`: the actual match rates, coverage, and current limitations from the real run.
- `results/external_postcode_features.parquet` / `.csv`: the postcode dimension table after merging
  all three sources (parquet recommended).
- `results/data_dictionary.csv`: the source, definition, and unit for each of the 141 fields.
- `results/postcode_source_patterns.csv`: the distribution of which combination of sources each
  postcode matched.
- `results/consumer_coverage.csv`, `consumer_feature_missingness.csv`, `consumer_postcode_exceptions.csv`:
  the match rate, per-field missingness rate, and list of postcodes that didn't fully match, after
  joining onto consumers.
- `results/transaction_coverage.csv`, `transaction_feature_missingness.csv`, `transaction_postcode_exceptions.csv`:
  the match rate (by row count and by amount), and list of postcodes that didn't fully match, after
  joining onto transactions.
- `results/transaction_coverage_by_state.csv`, `transaction_amount_by_match_status.csv`:
  the match rate split by the consumer's state, and the transaction-amount distribution split by
  match status (a review found that a single overall coverage rate can mask differences between
  states). Generated automatically by `write_transaction_diagnostics()` inside `integrate.py`'s
  `run()`, not a one-off script outside the pipeline, so it's reproducible; see VALIDATION.md.
- `results/metadata.json`: the path, SHA-256, and row count of each of the three source files, plus
  version info for this run.

## Running it

Run from the group repo root:

```bash
python -m pip install -r external_integration/requirements.txt
python external_integration/integrate.py \
  --consumers ../project-2-bnpl-tables-part1.zip \
  --transactions member2_curation/data/curated/curated_transactions \
  --output external_integration/results_rerun_new
python -m unittest discover -s external_integration/tests -v
```

The output directory must be empty; the re-run above writes to a new directory, don't overwrite
`results/`. `--transactions` points to member2's `curated_transactions` output (the parquet
partition folder); without it, only the consumer-layer join is done.

## Integration logic

1. `build_dimension`: reads `external_census/results/census_clean.parquet`,
   `external_seifa/results/seifa_clean.parquet`, and `external_ato/results/ato_clean.parquet`
   separately, checks that postcode is unique and non-null and that the reference year is singular
   (2021 for Census/SEIFA, 2021-22 for ATO), prefixes field names with their source, and does an
   outer join on postcode, keeping each source's match flag (`{source}_matched`) and a combined flag
   (`all_sources_matched`).
2. `enrich`: LEFT JOINs the dimension table onto the consumer or transaction data (on the
   normalised 4-digit postcode). Three checks run before/after the join: row count and unique-ID
   count unchanged, the original columns' values completely unchanged (a hash check, equivalent to a
   cell-by-cell comparison but far more memory-efficient at tens-of-millions-of-rows scale), and the
   amount total (transactions only) unchanged; any failed check raises an error and stops the run
   immediately, rather than just writing a report.
3. Every join produces: a match-rate report (`{scope}_coverage.csv`), per-field missingness
   (`{scope}_feature_missingness.csv`), and a list of postcodes that didn't fully match
   (`{scope}_postcode_exceptions.csv`).
4. The **consumer layer** and the **transaction layer** use dimension tables of different
   granularity: the consumer layer (~500k rows) uses the full 141-column external feature table; the
   transaction layer (~14M rows) uses a reduced dimension table by default (`reduce_dimension`:
   postcode + 4 match flags, without the 141 individual feature values) -- attaching all 141 columns
   to 14M transaction rows would be expensive in both compute and storage, and the project brief also
   explicitly said there's no need to store everything as one giant transaction table. What this
   transaction-layer step needs to establish is the match rate and whether row counts hold before/
   after the join, not each transaction's own regional feature values; the actual feature values are
   joined later, after merchant-level aggregation in step 3 (at that point it's a few thousand rows,
   not 14 million, so joining all 141 columns is cheap). Use `--transactions-full` if the transaction
   layer needs the full 141 columns too, but it's noticeably slower and the output is much larger.

## Current progress

- Postcode dimension table: done, 2,710 postcodes, 141 columns.
- Consumer-layer join: done, all 499,999 consumers kept (LEFT JOIN doesn't drop rows), combined
  three-source match rate 80.84%.
- Transaction-layer join: **done**, all 14,195,505 transactions kept, amount total unchanged
  before/after the join. Combined three-source match rate (by row count) 80.77%, by amount basis
  80.77%, very close to the consumer layer's 80.84%. Uses the reduced dimension table (see above).
- `curated_transactions` (member2's actual transaction data, 14M+ rows) had never been obtained
  before this: only audit/summary CSVs existed on GitHub and locally, not the data itself. It turned
  out the raw transaction snapshot data was released to the whole group/class uniformly for this
  course (Canvas "Dataset Release"), not something unique to member2; using the
  `project-2-bnpl-tables-part2/3/4.zip` (raw transaction snapshots) you downloaded from Canvas plus
  member2's cleaning code already committed to GitHub (`member2_curation/src/`),
  `curated_transactions` was regenerated locally, and the resulting row count, deduplication result,
  merchant match count, and p99 amount all exactly match member2's original
  `curation_metadata.json` (reproducible). This data itself is large (790MB) and has already been
  excluded via `.gitignore`, so it won't be committed to GitHub.

## Source and time boundaries

- Census and SEIFA data reuse the teammates' already-cleaned versions; this module does not
  re-clean them, only renaming fields, tagging their source, and aligning postcodes.
- ATO data references `external_ato/results/ato_clean.parquet`; see that module's own
  README/VALIDATION for its basis.
- The three sources are the 2021 Census, 2021 SEIFA, and the 2021-22 ATO income year, which don't
  fully share the same basis or collection point in time -- they are used only as retrospective
  regional background, not as information that was known at the time a transaction occurred.
- ATO postcodes and ABS POAs both use the postcode (POA) scheme; no SA2 conversion was done.

## Geospatial visualisation (`postcode_map/`)

Everything above is a table (coverage %, missingness counts, feature CSVs) -- there wasn't an actual
map. This adds one: Australia's postcodes plotted using official ABS POA (2021) boundary data,
coloured by SEIFA IRSD relative-disadvantage decile, plus zoomed-in insets for Sydney, Melbourne,
Brisbane and Perth (national scale hides small inner-city postcodes).

See `postcode_map/README.md` for the data sources, the boundary-to-feature-table match coverage
(2,475 of 2,513 boundaries matched, 98.5%), and how to reproduce it.
