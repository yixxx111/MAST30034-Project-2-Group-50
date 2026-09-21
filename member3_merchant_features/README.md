# merchant_features

Builds the merchant-level feature table used to shortlist BNPL candidate merchants
(project step 3: "构造商户特征"). One row per `merchant_abn` that appears in
`member2_curation`'s curated transactions, combining:

- **Transaction scale & recency**: `total_transactions`, `total_revenue`, `first_order_date`,
  `last_order_date`, `active_months_all_time`.
- **Average order value (客单价)**: `avg_transaction_value`.
- **Repeat custom (复购)**: `repeat_consumer_share` — share of a merchant's distinct
  consumers who placed more than one order with it.
- **Revenue trend (增长)**: `normalized_monthly_revenue_trend` — the linear trend slope
  of monthly revenue, normalised by mean monthly revenue (`regr_slope / regr_avgy`).
  **This is a normalised slope, not a month-over-month growth rate** — don't read it as
  a percentage change.
- **Stability (稳定性)**: `monthly_revenue_cv` — coefficient of variation
  (`stddev_samp / avg`) of monthly revenue.
- **Customer-base regional/socio-economic profile**: transaction-weighted averages of a
  curated 13-field subset of the postcode-level Census/SEIFA/ATO features built in
  `external_integration`, joined via each transaction's `consumer_postcode`, plus a
  coverage rate **per source** (census/seifa/ato).
- **Merchant category / pricing / take rate**: parsed from `tables/tbl_merchants.parquet`'s
  `tags` field, plus `estimated_bnpl_revenue = total_revenue * take_rate_pct / 100`.

## Revenue trend & stability: fixed full-month window, zero-filled (fixed after review)

An earlier version computed the trend/CV over every month a merchant had a transaction,
skipping months with no transactions and including two partial calendar months. Both
turned out to matter:

- **Partial months included.** `curated_transactions` spans 2021-02-28 .. 2022-10-26, so
  2021-02 has data for 1 of 28 days and 2022-10 for 26 of 31. Left in, they distort a
  linear-trend slope.
- **Missing months silently skipped rather than counted as zero.** A merchant with
  revenue 100, 0, 100 in three consecutive months was previously scored on two 100s
  (looking artificially stable), not on the real 100/0/100 series.

Fixed by:
1. Restricting the calculation to a fixed **full-calendar-month window, 2021-03 to
   2022-09** (`WINDOW_START`/`WINDOW_END` in the script) — every day of every included
   month has data.
2. For each merchant, building an explicit month calendar from their first to last
   transaction **within that window** (never inventing months before their first-ever
   transaction) and filling any month with no transaction as **zero revenue**, not a
   gap.
3. Flagging low-confidence trend/CV estimates. **A second review found the original
   single flag (`potential_months_in_window < 3`, i.e. calendar span alone) missed
   merchants with very few actual transactions or few active months whenever their span
   happened to be long** — e.g. one merchant passed with only 2 transactions because its
   first and last order were 14 months apart. Concretely: 187 merchants had fewer than 10
   total transactions and 29 had only 1-2 active months in the window, and neither group
   was flagged by the old check. Fixed by splitting into three **independent** flags, any
   one of which can flag a merchant on its own:
   - `low_sample_short_window`: `potential_months_in_window < 3` (the original check)
   - `low_sample_few_transactions`: `transactions_in_window < 10` -- **a third review found
     this must use the transaction count INSIDE the growth window, not `total_transactions`
     (all-time)**, since the growth/CV estimate only ever looks at the window: 18 merchants
     had 10-12 transactions all-time but only 8-9 inside the window, so they weren't caught
     when the flag used the all-time count. `transactions_in_window` is now its own output
     column; `total_transactions` (all-time) is unchanged and still the scale metric.
   - `low_sample_few_active_months`: `active_months_in_window < 3`
   - `low_sample_growth_estimate`: true if **any** of the above three is true (220
     merchants total, up from 15 under the original single check)

   **These three thresholds (3 months / 10 transactions / 3 active months) are this
   team's own judgment call, not a statistical or industry standard.**
   `results/low_sample_threshold_comparison.csv` shows how many merchants each flag would
   catch under a few alternate thresholds, e.g. `low_sample_few_transactions` catches 85
   merchants at a threshold of 5, 220 at 10, 440 at 20 and 854 at 50 — the choice of
   threshold materially changes how conservative the flag is, and a stricter downstream
   ranking may prefer a higher cutoff.

This is a substantive fix, not cosmetic: re-running the old month-inclusive, non-zero-filled
version against a fixed window alone flips the trend's sign for 253 of 4,405 merchants
(reviewer's finding), and only 74 of the top 100 by trend overlap between the two versions.
After this fix, the fleet median `normalized_monthly_revenue_trend` is 0.0144 — matching
the reviewer's independent recomputation. `normalized_monthly_revenue_trend` and
`monthly_revenue_cv` are null exactly when `low_sample_short_window` is true (too few
calendar points to fit a slope at all); the other two flags mark a trend/CV value that
exists but rests on thin activity, not a null value.

New columns this adds: `potential_months_in_window` (denominator), `active_months_in_window`
(how many of those months had a real transaction), `low_sample_short_window`,
`low_sample_few_transactions`, `low_sample_few_active_months`, `low_sample_growth_estimate`.
`active_months_all_time` is kept separately for reference and is **not** the basis for the
trend/CV.

## External features: 13 fields, de-duplicated (fixed after review)

The original 16-field selection had several near-duplicate pairs a reviewer flagged by
correlation: regional population vs. total-family count (r≈0.99), a family-structure raw
count vs. total families (r≈0.97), and a SEIFA index's score vs. its own national decile
(r≈0.97 for IRSAD). Fixed by:

- Expressing family structure as **shares of total families**
  (`census_single_parent_family_share`, `census_couple_with_children_family_share`)
  instead of raw counts, so it no longer just re-encodes regional population size.
- Keeping only the **national decile** for each SEIFA index (IRSD, IRSAD, IER) and
  dropping the raw score, which is a near-linear transform of the decile at postcode
  granularity.

Remaining set (see `results/data_dictionary.csv` for exact definitions): Census
population, income, household size, the two family-structure shares, unemployment and
labour-force participation (7 fields); the three SEIFA deciles (3 fields); ATO average
income, salary and net-tax-payer share (3 fields) — 13 total. Each merchant's regional
profile is a **transaction-weighted average**: each transaction contributes once, based
on its consumer's postcode.

## Coverage is reported per source, by amount AND by row count (fixed after review)

Census, SEIFA and ATO are matched independently (same postcode join, three separate
match flags), so a single combined coverage rate doesn't tell you which specific columns
are how reliable. The table reports `census_data_coverage_rate`, `seifa_data_coverage_rate`,
`ato_data_coverage_rate` (each **revenue/amount-weighted**) alongside
`regional_data_coverage_rate_all_sources`.

**A second review found these amount-weighted coverage rates can look far higher than the
data actually backing a merchant's regional features are.** The regional feature averages
themselves (`census_population`, etc.) are **row-count-weighted** averages (every
transaction contributes equally), so a coverage figure meant to describe how much of that
average is backed by real external data should use the same weight — one large-dollar
matched transaction among many small unmatched ones can make the amount-weighted coverage
look high while the feature average is actually dominated by unmatched, imputed-as-null
transactions. Measured gap for individual merchants: up to **41.35 percentage points**
between ATO's amount- and row-count-weighted coverage. Fixed by adding a parallel
row-count-weighted column per source: `census_data_coverage_rate_by_count`,
`seifa_data_coverage_rate_by_count`, `ato_data_coverage_rate_by_count`,
`regional_data_coverage_rate_all_sources_by_count`. **Use the `_by_count` columns as the
primary figure when explaining how reliable the regional feature averages are** — the
amount-weighted columns are kept for reference/backward compatibility only.

**"Postcode matched" is not the same as "field usable".** A source being matched doesn't
guarantee every one of its 13 derived fields is non-null for that transaction (e.g. a
suppressed or undefined derived ratio). `results/feature_nonnull_rate_when_matched.csv`
reports, for each of the 13 selected features, the actual non-null rate among transactions
where its source was matched — in the last run all 13 fields were 99.1%-100% non-null when
matched, so the gap is small in practice, but it is now measured rather than assumed.

**ATO small-denominator flag propagated.** `external_ato`'s cleaning output flags postcodes
whose derived ATO ratios rest on fewer than 100 individuals (`ato_small_denominator`) as
statistically unstable. This was previously dropped when the dimension was reduced to the
13 selected features. It's now surfaced per merchant as
`ato_small_denominator_share_of_matched` — the share of a merchant's ATO-matched
transactions that come from a small-denominator postcode (fleet average ~9.7%). A high
value means the `ato_*` columns for that merchant, even though "matched", rest on
less-stable postcode-level ratios.

**Mean vs. median -- two separate questions (a third review pointed out the note above
answered the wrong one).**

1. *Why average across a merchant's customer postcodes with a mean (transaction-weighted),
   not a median?* Because every transaction's postcode should contribute proportionally to
   the merchant's overall customer-base profile, and a simple mean composes cleanly with
   the coverage-rate calculations above (both are sums over the same transaction set). A
   median-of-postcodes would need its own aggregation logic and doesn't have an obvious
   transaction-weighted analogue. This is the question this section originally answered.

2. *Why does `ato_taxable_income_or_loss_per_reporter` itself use ATO's implied MEAN
   (total taxable income / reporter count) rather than ATO's own separately published
   PER-POSTCODE MEDIAN taxable income (Table 8)?* This is the question actually raised,
   and it's a different layer -- it's about the postcode-level ATO feature `external_ato`
   builds, before any merchant-level aggregation happens at all. **We have not done this
   comparison.** `external_ato` already downloads and keeps the official Table 8
   median/mean candidates separately (`external_ato/median_reference/`, 2,317 postcodes)
   specifically so this comparison can be done, but its own README/VALIDATION are explicit
   that "不擅自决定采用均值还是中位数" (not deciding mean vs. median unilaterally) --
   the candidate table is not merged into the 21-column core output, and coverage/
   definitional differences between the two statistics (Table 6B totals vs. Table 8's own
   median, different eligible-reporter definitions) haven't been reconciled. Using the mean
   here is the current choice, not a claim that outlier sensitivity has been ruled out --
   that sensitivity comparison is simply not done yet.

## Handling merchants with no `tbl_merchants` record

4,422 distinct `merchant_abn` values appear in the curated transactions, but only 4,026
of those have a matching row in `tbl_merchants`. The other 396 are transaction-only
("orphan") merchants, kept in the output — the BNPL candidate pool should be drawn from
merchants with actual observed activity — but flagged `has_merchant_master_record = false`,
with `merchant_name`, `merchant_category`, `merchant_pricing_level`,
`merchant_take_rate_pct` and `estimated_bnpl_revenue` left null. **Whether these 396
should be eligible for the final recommendation list is a separate policy decision** —
they have no category or take rate, which most scoring approaches will need.

## Outputs (`results/`)

- `merchant_features.parquet` / `.csv` — the feature table (4,422 rows × 46 columns).
- `data_dictionary.csv` — one row per output field with its definition, unit and source.
- `low_sample_threshold_comparison.csv` — how many merchants each low-sample flag would
  catch under a few alternate thresholds (see above).
- `feature_nonnull_rate_when_matched.csv` — actual non-null rate of each of the 13
  selected features, conditional on its source being matched.
- `metadata.json` — run timestamp, external-source provenance, the growth window and
  low-sample thresholds, row counts, and a revenue-reconciliation check.

## Reproducing

```bash
cd merchant_features
python build_merchant_features.py --overwrite   # requires ../member2_curation/data/curated/curated_transactions
                                                 # and ../external_{census,seifa,ato}/results/*_clean.parquet
python -m unittest tests.test_build_merchant_features -v
```

`--overwrite` replaces an existing `results/` directory; omit it and the script refuses
to run if `results/` already has output in it (safety default).

**`--overwrite` is now safe by construction (fixed after review).** The previous version
deleted most of `results/`'s contents before validating anything, and could in principle
have been pointed at the project root or an input directory. The script now (a) refuses
outright if `--output` resolves to the project root or a known input directory (or an
ancestor of one), (b) builds the entire run in a temporary directory first, and (c) only
after every validation check in the script has passed (row-count bounds, the
revenue-reconciliation assertion, the 25-category assertion) does it replace the *specific,
named files this module is known to generate* in the real output directory — never a
blanket delete of everything there. A failed run leaves any previous good `results/`
untouched.

See `VALIDATION.md` for the checks the script runs, the reviewer's findings that drove
the fixes above, and the test results.
