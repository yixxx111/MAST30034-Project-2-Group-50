# External data integration validation report

Validation date: 2026-09-20 (main text), with supplementary statistics updated in this review. The
numbers below come from re-running the current code against the three sources' actual cleaned
outputs, and match the existing `results/` output in the repo field-by-field (reproducible).

## Postcode dimension table

- Input row counts from the three sources: census 2,641, seifa 2,641, ato 2,630 (each with a unique
  postcode and a single reference year).
- After the outer join, the dimension table has 2,710 rows, 141 columns (including 3 source match
  flags + 1 combined match flag + 1 postcode column).
- Which sources each postcode matched (`postcode_source_patterns.csv`):
  - Matched in all of census+seifa+ato: 2,561 postcodes
  - Only census+seifa (ato unmatched): 80 postcodes
  - Only ato (census+seifa unmatched): 69 postcodes
  - 2,561 + 80 + 69 = 2,710, no gaps and no duplicates.

## Consumer-layer join

- Input: 499,999 consumers; still 499,999 after the join (LEFT JOIN doesn't drop rows; row-count/
  unique-ID checks passed).
- Match rate (`consumer_coverage.csv`):
  - census_matched / seifa_matched: 83.36% (416,818 / 499,999)
  - ato_matched: 82.97% (414,825 / 499,999), matching the number in `external_ato/VALIDATION.md`
  - all_sources_matched: 80.84% (404,185 / 499,999), matching the "combined postcode coverage
    80.84%" in the ATO report
- Per-field missingness (`consumer_feature_missingness.csv`): of 141 fields, 136 have some
  missingness.

  **(Corrected in this review)** This previously said "the missingness rate is the complement of
  each source's match rate", attributing all missingness to "postcode not matched" -- that isn't
  accurate. `consumer_feature_missingness.csv` itself already records two separate columns:
  `missing_rows` (total rows missing for that field) and `matched_but_missing_rows` (**rows where
  the postcode DID match, but the field itself is still null**), and these are two different causes:
  - The `missing_rows - matched_but_missing_rows` portion is where the postcode itself didn't match
    its source, so missingness genuinely is the complement of the match rate -- that part of the
    reasoning holds.
  - The `matched_but_missing_rows` portion is where the source data itself has missingness on that
    field (e.g. some ABS/SEIFA derived metrics are suppressed or undefined for small-sample
    postcodes), unrelated to whether the postcode matched -- even a matched postcode can still have
    a null field.

  Checked directly: of the 136 fields with missingness, **64 fields have `matched_but_missing_rows`
  > 0**, i.e. "the source field/derived value itself is missing after matching", for example:

  | Field | Consumers matched but field still missing |
  |---|---:|
  | `census_avg_household_size` | 618 |
  | `seifa_irsd_national_decile` | 2,693 |
  | `seifa_irsd_state_decile` | 5,555 |

  This isn't necessarily a cleaning-step error (the source data itself may genuinely have these
  nulls), but it means the blanket statement "missingness is the complement of the match rate" isn't
  accurate and the two causes need to be distinguished. **These values are not zero-filled or
  imputed** -- keeping the genuine missingness is the right call; only the written description needed
  to distinguish the two causes accurately.
- List of postcodes that didn't fully match (`consumer_postcode_exceptions.csv`): e.g. postcode
  `0200` (unmatched in all three sources, 145 consumer records), `0801`/`0804`/`0811` (matched only
  ato, not covered by census/seifa), etc., all traceable.

## Transaction-layer join

`curated_transactions` was produced locally by re-running member2's cleaning code already committed
to GitHub (`member2_curation/src/`) against the raw transaction snapshot data released on Canvas
(`project-2-bnpl-tables-part2/3/4.zip`) -- see the reproduction notes under `member2_curation` for
details; the resulting `curation_metadata.json` numbers (input_rows 14,195,505, quarantined_rows 0,
unmatched_merchant_rows 580,830, amount_p99 1619.2727559488073) match member2's original run
exactly.

The transaction-layer external-data join was built on top of that:

- Input: 14,195,505 transactions; still 14,195,505 after the join (all four checks -- row count,
  unique order_id count, original-column content via hash check, and amount total -- passed,
  completely unchanged before/after the join).
- Match rate and amount coverage (`transaction_coverage.csv`):
  | Metric | By row count | By amount |
  |---|---|---|
  | census_matched / seifa_matched | 83.51% (11,855,228 / 14,195,505) | 83.52% |
  | ato_matched | 82.84% (11,760,045 / 14,195,505) | 82.84% |
  | all_sources_matched | 80.77% (11,466,314 / 14,195,505) | 80.77% |

  **(Corrected in this review)** The previous version said here "the row-count coverage and the
  amount coverage are almost equal, showing the unmatched transactions have no systematic bias in
  amount distribution" -- that inference doesn't hold: the two coverage rates being close, at most,
  shows the matched and unmatched groups have similar **average** transaction amounts; it doesn't
  prove there's no systematic difference in amount distribution, state distribution, or industry
  distribution. Replaced with the statement below, with actual measurements added:

  > The overall amount coverage is close to the row-count coverage, but this alone doesn't rule out
  > coverage differences within subgroups (by state, by merchant industry, etc.) -- those need to be
  > checked separately.

  Additional measurements (new in this review, unmatched vs. matched groups):

  | Group | n | p25 | p50 (median) | p75 | p99 | mean |
  |---|---:|---:|---:|---:|---:|---:|
  | Unmatched (all_sources_matched=false) | 2,729,191 | 26.17 | 62.20 | 150.43 | 1614.79 | 166.28 |
  | Matched (all_sources_matched=true) | 11,466,314 | 26.12 | 62.24 | 150.46 | 1620.51 | 166.22 |

  By amount distribution, the matched and unmatched groups really are very close (each percentile
  differs by only cents to a few dollars) -- **this specific conclusion holds up**. But split by
  state, the coverage differs a lot and is not uniform:

  | State | Transactions | all_sources_matched coverage |
  |---|---:|---:|
  | SA | 1,612,955 | 94.1% |
  | QLD | 2,100,381 | 92.6% |
  | TAS | 525,947 | 92.6% |
  | VIC | 3,280,823 | 90.9% |
  | ACT | 130,325 | 82.8% |
  | NT | 202,178 | 69.1% |
  | WA | 2,247,663 | 68.3% |
  | NSW | 4,095,233 | 67.2% |

  NSW, WA and NT have noticeably lower external-data coverage than VIC/QLD/SA/TAS (a 20+ percentage
  point gap), because these states have a higher proportion of postcodes that don't appear in all
  three of census/seifa/ato -- this isn't random missingness. **Any downstream analysis that slices
  by state or by a merchant's own state (including the step-4 industry-growth chart and later
  ranking models) should be aware of this**: external-feature coverage is inherently worse for
  NSW/WA/NT merchant customer bases, and "this merchant has no regional features" should not be
  conflated with "this merchant's regional features don't matter". The industry-level coverage
  breakdown is in `member3_merchant_features/VALIDATION.md` (which now reports coverage separately
  for the census/seifa/ato sources).

- The transaction layer uses the reduced dimension table (postcode + 4 match flags), without the
  141 individual external feature values: attaching 141 columns to 14M rows would be expensive in
  both compute and storage, and what this step actually needs is the match rate and row-count
  consistency, not each transaction's own regional features.

  **(Wording corrected in this review)** This previously said "the specific feature values are
  joined later, after merchant-level aggregation" -- that got the order backwards. What actually
  happens (`member3_merchant_features/build_merchant_features.py`) is: **external features are
  joined at the transaction layer first, by `consumer_postcode`** (this is what makes it possible to
  compute the transaction-layer match flags and coverage), **and only then are the transactions --
  which already carry the joined feature values -- aggregated by `merchant_abn` into a
  transaction-weighted average at merchant level** -- not "aggregate transactions to merchants
  first, then join postcodes onto merchants". This module (`external_integration`) only validates
  the match rate and row-count consistency for the transaction layer's "join first, keep only the
  flags" step; the actual 141 feature values are only genuinely attached once aggregated to merchant
  level (a few thousand rows, not 14 million) -- that step still follows the "join transactions
  first, then aggregate" order, it just does the join and the aggregation in the same query.
- List of postcodes that didn't fully match: `transaction_postcode_exceptions.csv`.
- `transaction_feature_missingness.csv` is empty: because the reduced dimension table has no field
  to report missingness for besides the 4 match flags (which end in `_matched` and are excluded from
  per-field missingness by definition) -- this is the expected result by design, not an omission.

## Data-integrity checks (built into the script, not an after-the-fact spot check)

- Consistency check of the original columns' content before/after the join: for the consumer layer
  (~500k rows), a cell-by-cell two-way comparison (EXCEPT ALL); for the transaction layer (~14M
  rows), an aggregated hash comparison over all original columns instead (`sum(hash(...))`) -- both
  verify the same thing (the join didn't change the original data), but the hash check is far
  cheaper at tens-of-millions-of-rows scale, avoiding the out-of-memory risk of a set-level two-way
  comparison over 14M rows at once.
- Row count and unique-ID count are completely unchanged before/after the join, confirming it's a
  LEFT JOIN and not an accidental many-to-many expansion.
- The transaction layer additionally checks the amount total is unchanged before/after the join
  (the consumer layer has no amount field, so this doesn't apply there).
- Any failed check raises an error and stops the run immediately, rather than just logging a
  warning.

## Review (round 2): two diagnostic tables previously had no generating code

**Finding:** `transaction_coverage_by_state.csv` and `transaction_amount_by_match_status.csv`
(which `member3_summary.ipynb` reads) were previously produced by a one-off device_bash script;
the numbers were independently verified as correct by a teammate, but the repo's `integrate.py` and
notebook code only read these two tables -- there was no code to generate them, so a teammate
re-running the whole pipeline from raw data couldn't reproduce them.

**Fix:** turned the generation logic into a proper function in `integrate.py`,
`write_transaction_diagnostics(con, out, joined_view='joined')`, called right after the
transaction-layer `enrich()` call inside `run()`, directly reusing the `joined` temp view that
`enrich()` leaves behind (which already has `consumer_state`, `dollar_value`,
`all_sources_matched`) -- no longer a one-off query outside the pipeline. Re-running the full
pipeline (`python integrate.py --consumers ../tables/tbl_consumer.csv --transactions
../member2_curation/data/curated/curated_transactions`) produced the same two tables with values
identical to before this review (state coverage and amount percentiles by match status match
exactly), confirming this only wired the generation back into the pipeline without changing any
numbers.

## Automated tests

Run: `python -m unittest discover -s external_integration/tests -v`
Actual result: 7 passed, none skipped (2 new, covering `write_transaction_diagnostics`: with
`consumer_state` present, both the by-state coverage and the by-match-status amount distribution are
written with correct values; without `consumer_state` (e.g. only the consumer-layer join was run),
it safely skips and writes no file).
Coverage: the three-source outer join and field prefixing, rejection of duplicate/multi-year/null
postcodes, transaction-layer join row-count/amount/missingness statistics, reading partitioned
parquet input, rejection of duplicate order_id, and the generation/skip logic for the two diagnostic
tables.

## Scope of use

- The three sources have different reference years (Census/SEIFA 2021, ATO 2021-22); once merged
  they serve only as retrospective regional background features, not as collected at the same point
  in time.
- `all_sources_matched` only means the postcode key exists in all three sources -- it doesn't mean
  every field is complete for that postcode (still check `consumer_feature_missingness.csv`).
- **External-data coverage is not evenly distributed by state** (NSW/WA/NT noticeably lower than
  other states, see table above); users should not assume the coverage gaps are random.
- This module does not do merchant-level feature construction, fraud analysis, or merchant scoring;
  those are out of scope for the integration stage.
