# Validation

## Review findings and how they were addressed

A teammate review of the first version raised three substantive issues. All three are
fixed in the current script; this section records what was found and what changed.

### 1. Partial months and skipped-zero months distorted growth/CV

**Finding:** the trend/CV calculation used every month a merchant had a transaction,
including 2021-02 (1/28 days of data) and 2022-10 (26/31 days), and silently skipped
months with no transactions instead of counting them as zero revenue. Re-running the
same formula over a fixed full-month window (2021-03 to 2022-09) changed the trend's
sign for 253 of 4,405 merchants, and a top-100-by-trend selection only overlapped 74/100
between the two versions.

**Fix:** `WINDOW_START = (2021, 3)`, `WINDOW_END = (2022, 9)` — the only months where
100% of calendar days have data. For each merchant, an explicit calendar of months is
built from their first to last transaction *within that window*, with any month that has
no transaction filled as **zero revenue** (a real data point, not a gap). Merchants with
fewer than 3 months in the window are flagged `low_sample_growth_estimate` (15 merchants,
all with exactly 1 transaction month; their trend/CV are null).

**Result of the fix:** fleet median `normalized_monthly_revenue_trend` = **0.0144**,
matching the reviewer's independent recomputation almost exactly. The field was also
renamed from `monthly_revenue_growth_rate` to `normalized_monthly_revenue_trend` since it
is a normalised regression slope, not a month-over-month growth rate.

### 2. Redundant regional features

**Finding:** correlation checks on the original 16-field set found near-duplicate pairs:
regional population vs. total-family count (r≈0.99), a family-structure raw count vs.
total families (r≈0.97), and a SEIFA index's own score vs. its national decile (r≈0.97
for IRSAD). Ranking on these as if independent double-counts the same signal.

**Fix:** family-structure counts are now shares of total families
(`census_single_parent_family_share`, `census_couple_with_children_family_share`); each
SEIFA index keeps only its national decile. Post-fix correlation of
`census_population` with the two family-share fields: **0.282 and 0.194** (was ~0.97-0.99
with the raw counts) — the redundancy is gone. `seifa_irsd_national_decile` and
`seifa_ier_national_decile` still correlate at 0.798, which is expected (both are
legitimate, related-but-distinct socio-economic constructs) and is not the kind of
one-column-encodes-another redundancy the review flagged.

Feature count: 16 -> **13**.

### 3. Coverage was a single blended figure

**Finding:** `regional_data_revenue_coverage_rate` covered all three sources at once, but
census/seifa/ato are matched independently, so it didn't tell you the reliability of any
specific column.

**Fix:** coverage is now reported per source, revenue-weighted:

| Coverage field | Fleet-average value |
|---|---:|
| `census_data_coverage_rate` | 83.5% |
| `seifa_data_coverage_rate` | 83.5% |
| `ato_data_coverage_rate` | 82.7% |
| `regional_data_coverage_rate_all_sources` | 80.6% |

(Census and SEIFA coverage match exactly because both are keyed to the same ABS POA
postcode list; ATO's slightly lower coverage reflects its own source postcode list.)

## Second review: further fixes

A follow-up review of the fixes above found five more concrete issues. All are fixed;
here's what changed and the numbers that back it up.

### 4. Low-sample flag missed thin-activity merchants with a long calendar span

**Finding:** the single `potential_months_in_window < 3` check missed 187 merchants with
fewer than 10 total transactions and 29 merchants with only 1-2 active months in the
window, because a long first-to-last-transaction span can coexist with very little actual
activity (one merchant with only 2 transactions passed because its span was 14 months).

**Fix:** three independent flags — `low_sample_short_window` (< 3 potential months),
`low_sample_few_transactions` (< 10 total transactions), `low_sample_few_active_months`
(< 3 active months in the window) — with `low_sample_growth_estimate` true if any of the
three is true. Result of the fix (last run):

| Flag | Merchants flagged |
|---|---:|
| `low_sample_short_window` | 15 |
| `low_sample_few_transactions` | 220 |
| `low_sample_few_active_months` | 44 |
| `low_sample_growth_estimate` (any of the above) | 220 |

Thresholds are this team's judgment call; `results/low_sample_threshold_comparison.csv`
shows the sensitivity (e.g. `low_sample_few_transactions` catches 94/220/440/854 merchants
at thresholds of 5/10/20/50).

### 5. Coverage-rate weighting didn't match the feature-average weighting

**Finding:** the regional feature averages are row-count-weighted, but the reported
coverage rates were amount-weighted, which can make coverage look higher than the data
actually backing the averages. Measured max gap for ATO, one merchant: **41.35 percentage
points** (reproduced from this run: `(df.ato_data_coverage_rate -
df.ato_data_coverage_rate_by_count).abs().max()` = 0.4135).

**Fix:** added `census_data_coverage_rate_by_count`, `seifa_data_coverage_rate_by_count`,
`ato_data_coverage_rate_by_count`, `regional_data_coverage_rate_all_sources_by_count` —
row-count-weighted, matching the feature-average weighting, and now the primary figure for
explaining reliability. Also added `results/feature_nonnull_rate_when_matched.csv`,
checking each of the 13 features' actual non-null rate conditional on its source being
matched (not assuming "matched" implies "usable"): all 13 fields are 99.1%-100% non-null
when matched in the last run — the gap exists but is small.

### 6. Two diagnostic CSVs had no generation code in the pipeline

**Finding:** `external_integration/results/transaction_coverage_by_state.csv` and
`transaction_amount_by_match_status.csv` (referenced by `member3_summary.ipynb`) were
generated by one-off ad-hoc queries, not by any committed pipeline script — numbers were
correct but not reproducible from raw data.

**Fix:** moved into `external_integration/integrate.py` as a proper function,
`write_transaction_diagnostics()`, called from `run()` right after the transaction-scope
`enrich()` call, with its own unit tests
(`external_integration/tests/test_integration.py`). See `external_integration/VALIDATION.md`
for details; this only affects that module's pipeline, not this one.

### 7. October chart wording overclaimed / mislabeled

Applies to `industry_growth`, not this module — see `industry_growth/VALIDATION.md`.

### 8. `--overwrite` deleted too broadly and too early

**Finding:** the previous `--overwrite` deleted most of `results/`'s contents before any
input validation, and `--output` could in principle point at the project root or an input
directory.

**Fix:** `_assert_safe_output_dir()` refuses an `--output` that resolves to the project
root or a known input directory (or an ancestor of one) — tested in
`tests/test_build_merchant_features.py::SafeOutputDirTests`. The whole run now builds into
a temporary directory first; only after every validation assertion in the script has
passed does `_safe_replace_output()` move the specific, named files this module generates
(`KNOWN_OUTPUT_FILES`) into the real output directory. A failed run never touches an
existing good `results/`.

### Closing items

- **ATO small-denominator flag propagated**: `ato_small_denominator_share_of_matched`
  (share of a merchant's ATO-matched transactions from a small-denominator postcode;
  fleet mean ~9.7%, last run).
- **Mean vs. median justification**: added to README.md, then corrected by a third review
  which found it answered the wrong question -- it explained why transaction-weighted MEAN
  is used to aggregate ACROSS a merchant's customer postcodes (a settled, defensible
  choice), not why `ato_taxable_income_or_loss_per_reporter` itself uses ATO's implied mean
  rather than ATO's own published PER-POSTCODE median (Table 8). Corrected: that
  comparison has not been done; `external_ato/median_reference/` already holds the official
  median/mean candidates precisely so it can be, but neither module claims the mean has
  been shown robust to outliers -- see README.md for the two-question breakdown.

## Third review: low-sample flag used the wrong transaction count

**Finding:** `low_sample_few_transactions` used `total_transactions` (all-time), but the
growth/CV estimate only ever looks at the fixed window (2021-03..2022-09). 18 merchants
had 10-12 transactions all-time but only 8-9 transactions actually inside the window --
enough to pass the old (all-time) check while the growth estimate itself rested on fewer
than 10 in-window data points.

**Fix:** added `transactions_in_window` (count of a merchant's transactions inside the
growth window) as its own output column, and `low_sample_few_transactions` now checks
`transactions_in_window < 10` instead of `total_transactions < 10`. `total_transactions`
(all-time) is unchanged and remains the scale metric elsewhere in the table.

**Verification:** re-running with the fix flags exactly the 18 merchants the reviewer
described (`transactions_in_window < 10 AND total_transactions >= 10`), all newly caught
by `low_sample_few_transactions`; `transactions_in_window <= total_transactions` holds for
every merchant (enforced as a regression test). `low_sample_few_transactions` count moved
from 202 to 220 (+18, exactly the reviewer's number); `low_sample_growth_estimate` moved
from 202 to 220 accordingly (`low_sample_short_window` and `low_sample_few_active_months`
are unaffected by this fix).

## Checks performed inside `build_merchant_features.py`

| Check | What it guards against |
|---|---|
| Selected external features all exist on the built dimension | A typo'd or renamed column silently dropping a feature |
| Output row count is between 4,000 and 5,000 | A join exploding or collapsing rows unexpectedly (actual: 4,422) |
| `sum(merchant_features.total_revenue) == sum(curated_transactions.dollar_value)` (to $0.01) | The transaction→merchant aggregation losing or double-counting revenue |
| Tag parsing produces zero nulls | The regex failing to match a `tbl_merchants.tags` value |
| Distinct `merchant_category` count == 25 | Case/whitespace variants fragmenting what the project overview calls the "原始25类" |

## Result of the last run

```
output_rows: 4422
merchants_with_master_record: 4026
merchants_without_master_record: 396
merchants_flagged_low_sample_growth: 220 (short_window=15, few_transactions=220, few_active_months=44)
distinct_merchant_categories: 25
growth_window: 2021-03 .. 2022-09
feature_nonnull_rate_when_matched_min: 0.9914
sum_merchant_total_revenue:   2359703946.3588085
sum_transaction_dollar_value: 2359703946.3587780   (matches to the cent; residual is float summation order)
```

## Unit / invariant tests (`tests/test_build_merchant_features.py`)

28 tests, all passing:

- **Tag parsing** (4): mixed bracket styles, whitespace/case collapsing, malformed/missing
  tag handling.
- **Window config** (4): the growth window is exactly 2021-03..2022-09; the low-sample
  thresholds are sane.
- **Output invariants** (17, run against the actual `results/merchant_features.parquet`):
  one row per merchant; exactly 25 categories; orphan merchants have null
  category/pricing/take-rate/estimated-revenue and matched merchants don't; every
  selected external feature is present; the two family-share fields are proportions in
  [0,1]; the raw family counts and SEIFA scores this review flagged are confirmed **absent**
  from the output (regression guard against reintroducing them); all coverage rates
  (amount- and count-weighted) and `ato_small_denominator_share_of_matched` are in [0,1];
  scale metrics are strictly positive; each of the three low-sample flags independently
  matches its own threshold; `low_sample_growth_estimate` equals the OR of the three;
  a merchant with few transactions but a long span is still caught (regression guard for
  the second review's core finding); `low_sample_few_transactions` matches
  `transactions_in_window` (not `total_transactions`) against its threshold, and
  `transactions_in_window` never exceeds `total_transactions` (regression guards for the
  third review); `normalized_monthly_revenue_trend` is null if and
  only if a merchant is flagged `low_sample_short_window`; `active_months_in_window` never
  exceeds `potential_months_in_window`.
- **Safe output dir** (3): `--output` pointed at the project root or the transactions
  input directory is refused; the default `results/` dir is allowed.

```
$ python -m unittest tests.test_build_merchant_features -v
...
Ran 28 tests in 0.04s
OK
```

## Known, documented edge cases (not treated as failures)

- **220 merchants** are flagged `low_sample_growth_estimate` under at least one of the
  three independent criteria (15 short-window, 220 few-transactions [`transactions_in_window`
  based, per the third review below], 44 few-active-months — the same merchant can trigger
  more than one) — `normalized_monthly_revenue_trend` and `monthly_revenue_cv` are null
  specifically for the 15 short-window merchants, and should be treated with extra caution
  (or excluded) for all 220 in any ranking. See "Second review" and "Third review" above
  for the full comparison of alternate thresholds.
- **396 merchants** (present in transactions, absent from `tbl_merchants`) have null
  category/pricing-level/take-rate/estimated-revenue by design — see README.md. Whether
  they should be eligible for the final recommendation list is left as an open policy
  question for whoever builds the ranking, since they lack the category/take-rate most
  scoring approaches need.
- A handful of merchants have all their revenue from postcodes that never matched any
  external source, so their `census_*`/`seifa_*`/`ato_*` columns are null even though
  `has_merchant_master_record` is true — reflected honestly by the per-source coverage
  rates rather than silently imputed.
