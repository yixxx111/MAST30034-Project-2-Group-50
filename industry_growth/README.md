# industry_growth

Step 4: industry growth visualization. Groups merchants into the project's 5 agreed
industry groups and visualizes revenue trends for **this dataset's sample merchants**
over the curated transaction history. This is a sample indicator, not a claim about the
whole Australian retail industry — every chart title says so explicitly.

## Industry groups

The 25 MCC-style merchant categories (parsed in `merchant_features`) are grouped as
follows -- the project's agreed grouping (see `category_to_group_mapping.csv` for the
full category-level mapping), reconstructed here from the merchant-count breakdown
already agreed on and verified to match exactly against `tbl_merchants`:

| Industry group | Merchants | Share |
|---|---:|---:|
| Digital, Technology & Communications | 867 | 21.5% |
| Home, Garden & Living | 827 | 20.5% |
| Mobility, Health & Specialist Services | 806 | 20.0% |
| Creative, Books & Leisure | 827 | 20.5% |
| Art, Gifts, Jewellery & Fashion | 699 | 17.4% |
| **Unclassified** (no `tbl_merchants` record, kept for audit only -- not a 6th official segment) | 396 | -- |

## Charts (`results/charts/`)

- `industry_growth_indexed.png` -- monthly revenue per group, indexed to 100 at each
  group's first **full** month (2021-03). Full months (2021-03..2022-09) are solid
  lines; the trailing partial month (2022-10) is shown as a separate hollow marker
  connected by a dashed line and annotated "partial-month actual" (a second review
  found the original wording, "projection", was factually wrong -- it's an observed
  value through Oct 26, not a forecast) -- not folded into the trend as if it were an
  ordinary data point. Shows a clear seasonal peak in Nov-Dec 2021 (~180 index) before
  falling back and growing again through 2022.
- `industry_revenue_share.png` -- total revenue by group, **full months only**
  (2021-03..2022-09), sorted descending. Home, Garden & Living is largest (22.0%),
  Art, Gifts, Jewellery & Fashion smallest of the 5 classified groups (14.9%).
- `industry_sep_vs_oct_daily_avg.png` -- Sep vs Oct 2022 **average daily** revenue by
  group. See the data-quality note below for why this chart exists.

## Data-quality note: October 2022's lower total reflects incomplete observation (fixed after two rounds of review)

`curated_transactions` spans 2021-02-28 .. 2022-10-26. Two months aren't fully covered:
2021-02 (1/28 days) and 2022-10 (26/31 days). An earlier version of this analysis used a
coverage threshold that excluded 2021-02 but *kept* 2022-10 as an ordinary month.

**A reviewer found this was misleading**: October's transaction **total** is ~8-10%
below September's for every group, but October's **average daily** revenue is actually
**~3.9-6.7% higher** than September's for every group (`industry_sep_vs_oct_daily_avg.png`).

**A second review found the fix's own wording still overclaimed.** "The dip is purely a
day-count artifact, not a business decline" goes beyond what a partial month can support:
only the OBSERVED daily average being higher is confirmed; what a complete October would
have shown is unknown. The calibrated statement used everywhere now (chart
annotation/title, this README, VALIDATION.md, `member3_summary.ipynb`) is: **"October's
total is affected by incomplete observation and cannot be directly read as a business
decline; the observed daily average during this period is higher."** Fixed by:

- Both partial months are now excluded from the fixed analysis window
  (`WINDOW_START=2021-03`, `WINDOW_END=2022-09` -- matching `merchant_features`'
  window exactly), which drives the growth line, the trend/CV summary, and the
  revenue-share chart.
- October is **kept in the underlying data** (`industry_monthly_revenue.csv`,
  `is_full_month=False`) and shown on the growth chart as a visually distinct
  partial/projected point, not silently included or silently dropped.
- The new Sep-vs-Oct daily-average chart makes the actual direction (up, not down)
  visible rather than only the misleading month-total.
- A hard assertion in the script checks that exactly `{2021-02, 2022-10}` are the
  partial months; if the underlying data changes, the script fails loudly rather than
  silently keeping a stale window.

## Outputs (`results/`)

- `industry_monthly_revenue.csv` / `.parquet` -- one row per (industry_group,
  order_year, order_month), **including both partial months**: revenue, transactions,
  active merchants, days_with_data, avg_daily_revenue, is_full_month, and the indexed
  revenue used in the growth chart.
- `industry_growth_summary.csv` -- one row per group, computed over full months only:
  total revenue, revenue share, merchant count, trend/CV (same definitions as
  `merchant_features`, aggregated to group level).
- `category_to_group_mapping.csv` -- the 25-category -> 5-group lookup.
- `data_dictionary.csv`, `metadata.json` (includes the Sep->Oct daily-avg % change by
  group).

## Reproducing

```bash
cd industry_growth
python build_industry_growth.py --overwrite   # requires ../member2_curation/data/curated/curated_transactions
                                               # and ../merchant_features/results/merchant_features.parquet
python -m unittest tests.test_build_industry_growth -v
```

**`--overwrite` is now safe by construction (fixed after review).** The previous version
deleted the entire `--output` directory outright before any input validation. It now
refuses outright if `--output` resolves to the project root or a known input path (or an
ancestor of one), builds the whole run in a temporary directory first, and only after
every validation check in the script has passed does it replace the specific, named
files/dirs this module generates in the real output directory. A failed run leaves any
previous good `results/` untouched.
