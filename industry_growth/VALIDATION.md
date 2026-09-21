# Validation

## Review finding and fix: October's month-total dip reflects incomplete observation

**Finding:** the previous version's <50%-day-coverage threshold kept October 2022
(26/31 days, 84% coverage) as an ordinary month. Comparing actual month totals: October
was ~8-10% below September for the five official groups. But per-day average revenue
tells the opposite story.

**Verification (this run, `industry_sep_vs_oct_daily_avg_pct_change_by_group` in
metadata.json):**

| Industry group | Sep -> Oct daily-avg change |
|---|---:|
| Home, Garden & Living | +3.9% |
| Art, Gifts, Jewellery & Fashion | +4.0% |
| Digital, Technology & Communications | +4.4% |
| Mobility, Health & Specialist Services | +4.7% |
| Creative, Books & Leisure | +5.7% |
| Unclassified | +6.7% |

Every group's daily average **rose**. Fix: the growth line/trend/revenue-share now use
only the fixed full-month window 2021-03..2022-09 (matching `merchant_features`); October
is kept in the raw data and shown as a clearly separate point on the growth chart, plus a
dedicated chart makes the daily-average direction visible.

**Second review: the fix's own wording still overclaimed.** The chart annotation called
the October point a "projection" -- it is an actual observation through Oct 26, not a
forecast, so it's now labelled "partial-month actual". And "the dip is purely a day-count
artifact, not a business decline" claims more than a partial month can support: only the
OBSERVED daily average being higher is confirmed, not what a complete October would have
shown. Every occurrence of this conclusion (chart annotation, chart 3's title, this file,
README.md, `member3_summary.ipynb`) now reads: **"October's total is affected by
incomplete observation and cannot be directly read as a business decline; the observed
daily average during this period is higher."** No remodeling was needed -- text/label
changes only, followed by regenerating the charts and notebook.

## Checks performed inside `build_industry_growth.py`

| Check | What it guards against |
|---|---|
| Every `merchant_category` maps to exactly one group, and vice versa | A category silently falling into no group, or a stale mapping entry |
| `Unclassified` count == count of merchants with no `tbl_merchants` record | Grouping logic double-counting or dropping orphan merchants |
| `sum(monthly panel revenue) == sum(all transactions)` (to $0.01) | The group-level aggregation losing or double-counting revenue |
| Partial-month set == exactly `{(2021,2), (2022,10)}` (hard assertion) | The window silently going stale if the underlying data changes; fails loudly instead |

## Result of the last run

```
full_month_window: 2021-03 .. 2022-09
partial_months: [(2021, 2), (2022, 10)]
total_revenue_all_months:        2,359,703,946.36
total_revenue_full_months_only:  2,234,541,395.36
```

Revenue share by group (full months only, 2021-03 to 2022-09):

| Industry group | Total revenue (AUD) | Share |
|---|---:|---:|
| Home, Garden & Living | 491,071,500 | 22.0% |
| Creative, Books & Leisure | 421,972,400 | 18.9% |
| Digital, Technology & Communications | 413,788,400 | 18.5% |
| Mobility, Health & Specialist Services | 383,097,600 | 17.1% |
| Art, Gifts, Jewellery & Fashion | 332,694,500 | 14.9% |
| Unclassified | 191,916,900 | 8.6% |

## Unit / invariant tests (`tests/test_build_industry_growth.py`)

13 tests, all passing:

- **Mapping tests** (2): all 25 categories map to exactly one of the 5 groups; merchant
  counts per group match the agreed breakdown (867/827/806/827/699), cross-checked
  against `tbl_merchants`.
- **Window config** (1): the full-month window matches `merchant_features` exactly
  (2021-03..2022-09).
- **Output invariants** (7): both partial months (2021-02, 2022-10) are present in the
  panel but flagged `is_full_month=False`; every group's revenue index starts at exactly
  100 in its first full month; revenue shares sum to 1; all 6 groups appear in the
  summary; every monthly revenue figure is positive; `avg_daily_revenue` is internally
  consistent with `revenue`/`days_with_data`; **October's average daily revenue is >=
  September's for every group** -- the exact claim the review made, now enforced as a
  regression test.
- **Safe output dir** (3, added after the second review): `--output` pointed at the
  project root or the transactions input directory is refused; the default `results/`
  dir is allowed.

```
$ python -m unittest tests.test_build_industry_growth -v
...
Ran 13 tests in 0.02s
OK
```

## Known, documented scope limits (not treated as failures)

- **All figures describe this dataset's sample merchants only.** Chart titles and the
  data dictionary say so explicitly; this is not an economy-wide or ABS-comparable
  industry-growth statistic.
- **2021-02 and 2022-10 are excluded from the growth line/trend/revenue-share**, kept
  in the raw CSV/parquet with `is_full_month=False` for anyone who wants to inspect them
  directly.
- **Unclassified (396 merchants) stays a separate audited bucket**, not merged into one
  of the 5 named groups and not presented as an official 6th business segment.
