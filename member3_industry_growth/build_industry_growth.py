"""Step 4: industry growth visualization.

Groups the 25 MCC-style merchant categories (parsed in merchant_features) into 5
broader industry groups (plus an "Unclassified" bucket for the 396 merchants with no
tbl_merchants record, kept for audit only -- it is not treated as a 6th official
business segment), then visualizes revenue trends for THIS DATASET's sample merchants
by group across the curated transaction history. This is a sample-merchant indicator,
not a claim about the whole Australian retail industry.

Design choice -- category grouping:
    The 25 categories are standard MCC (Merchant Category Code) retail classifications.
    They are grouped into the project's 5 agreed industry groups below (Digital,
    Technology & Communications / Home, Garden & Living / Mobility, Health &
    Specialist Services / Creative, Books & Leisure / Art, Gifts, Jewellery &
    Fashion), reconstructed here from the merchant-count breakdown the group
    already agreed on (867 / 827 / 806 / 827 / 699 merchants respectively, out of
    4,026 -- verified to match exactly against tbl_merchants category counts).

Data-quality / partial-month handling (see VALIDATION.md for the full writeup):
    The transaction data spans 2021-02-28 .. 2022-10-26. Two calendar months are not
    fully covered: 2021-02 (1/28 days) and 2022-10 (26/31 days). A reviewer found that,
    left in as ordinary months, 2022-10 makes the growth chart show an ~8-10% MONTH-TOTAL
    decline versus September -- while the per-day average revenue for the same month
    actually *rises* ~4-6%. Calibrated conclusion: October's total is affected by
    incomplete observation and cannot be directly read as a business decline; the
    OBSERVED daily average during this period is higher. What a complete October would
    have shown is unknown -- this is not a claim that there was no decline, only that the
    month-total figure alone cannot support one. Fix:
      - The main growth line and the revenue-share chart use only full calendar months
        (2021-03 .. 2022-09, matching merchant_features' WINDOW_START/WINDOW_END).
      - 2022-10 is kept in the data (industry_monthly_revenue.csv marks it
        is_full_month=False) and shown on the growth chart as a separate, visually
        distinct trailing point representing the actual (not projected/forecast) partial-
        month observation through Oct 26, not connected by a solid line.
      - A third chart compares September vs. October average DAILY revenue per group,
        so the month-total dip and the daily-average rise are both visible rather than
        only the (misleading) month-total.

Outputs:
    - industry_monthly_revenue.csv / .parquet: group x (order_year, order_month) panel,
      covering ALL months including the two partial ones, each flagged is_full_month.
    - industry_growth_summary.csv: one row per group, computed over full months only.
    - charts/industry_growth_indexed.png, charts/industry_revenue_share.png,
      charts/industry_sep_vs_oct_daily_avg.png.
    - category_to_group_mapping.csv, data_dictionary.csv, metadata.json.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

t0 = time.time()


def log(msg):
    print(f"{time.time()-t0:.2f}s {msg}", flush=True)


ROOT = Path(__file__).resolve().parent.parent
TRANSACTIONS = ROOT / "member2_curation/data/curated/curated_transactions"
MERCHANT_FEATURES = ROOT / "member3_merchant_features/results/merchant_features.parquet"
DEFAULT_OUTPUT = ROOT / "member3_industry_growth/results"

# Same fixed full-month window as merchant_features (build_merchant_features.WINDOW_START/END),
# repeated here rather than imported since the two modules are otherwise independent.
WINDOW_START = (2021, 3)
WINDOW_END = (2022, 9)
WINDOW_START_IDX = WINDOW_START[0] * 12 + WINDOW_START[1]
WINDOW_END_IDX = WINDOW_END[0] * 12 + WINDOW_END[1]

# ---------------------------------------------------------------------------
# 25 MCC-style categories -> 5 industry groups (+ "Unclassified" for the 396
# merchants with no tbl_merchants record). This is the group's own agreed grouping
# (confirmed against tbl_merchants merchant counts per category: 867 / 827 / 806 /
# 827 / 699 = 4,026, matching exactly), not one derived by this script.
# Every category from merchant_features.merchant_category must appear exactly once.
# ---------------------------------------------------------------------------
CATEGORY_TO_GROUP = {
    # 1. Digital, Technology & Communications -- 867 merchants (21.5%)
    "cable, satellite, and other pay television and radio services": "Digital, Technology & Communications",
    "computer programming , data processing, and integrated systems design services": "Digital, Technology & Communications",
    "computers, computer peripheral equipment, and software": "Digital, Technology & Communications",
    "digital goods: books, movies, music": "Digital, Technology & Communications",
    "telecom": "Digital, Technology & Communications",
    # 2. Home, Garden & Living -- 827 merchants (20.5%)
    "equipment, tool, furniture, and appliance rent al and leasing": "Home, Garden & Living",
    "florists supplies, nursery stock, and flowers": "Home, Garden & Living",
    "furniture, home furnishings and equipment shops, and manufacturers, except appliances": "Home, Garden & Living",
    "lawn and garden supply outlets, including nurseries": "Home, Garden & Living",
    "tent and awning shops": "Home, Garden & Living",
    # 3. Mobility, Health & Specialist Services -- 806 merchants (20.0%)
    "bicycle shops - sales and service": "Mobility, Health & Specialist Services",
    "health and beauty spas": "Mobility, Health & Specialist Services",
    "motor vehicle supplies and new parts": "Mobility, Health & Specialist Services",
    "opticians, optical goods, and eyeglasses": "Mobility, Health & Specialist Services",
    "watch, clock, and jewelry repair shops": "Mobility, Health & Specialist Services",
    # 4. Creative, Books & Leisure -- 827 merchants (20.5%)
    "artist supply and craft shops": "Creative, Books & Leisure",
    "books, periodicals, and newspapers": "Creative, Books & Leisure",
    "hobby, toy and game shops": "Creative, Books & Leisure",
    "music shops - musical instruments, pianos, and sheet music": "Creative, Books & Leisure",
    "stationery, office supplies and printing and writing paper": "Creative, Books & Leisure",
    # 5. Art, Gifts, Jewellery & Fashion -- 699 merchants (17.4%)
    "antique shops - sales, repairs, and restoration services": "Art, Gifts, Jewellery & Fashion",
    "art dealers and galleries": "Art, Gifts, Jewellery & Fashion",
    "gift, card, novelty, and souvenir shops": "Art, Gifts, Jewellery & Fashion",
    "jewelry, watch, clock, and silverware shops": "Art, Gifts, Jewellery & Fashion",
    "shoe shops": "Art, Gifts, Jewellery & Fashion",
}

GROUP_ORDER = [
    "Digital, Technology & Communications",
    "Home, Garden & Living",
    "Mobility, Health & Specialist Services",
    "Creative, Books & Leisure",
    "Art, Gifts, Jewellery & Fashion",
    "Unclassified",
]

# Okabe-Ito colorblind-safe palette, assigned in a fixed order (never cycled/reused by rank).
GROUP_COLORS = {
    "Digital, Technology & Communications": "#0072B2",
    "Home, Garden & Living": "#E69F00",
    "Mobility, Health & Specialist Services": "#009E73",
    "Creative, Books & Leisure": "#CC79A7",
    "Art, Gifts, Jewellery & Fashion": "#D55E00",
    "Unclassified": "#7F7F7F",
}


# Files/dirs this module is specifically known to generate. --overwrite only ever
# touches these (plus a freshly rebuilt charts/), never a blanket rmtree of the whole
# output directory -- a review found the previous version deleted the entire --output
# directory outright, before any input validation.
KNOWN_OUTPUT_FILES = [
    "industry_monthly_revenue.csv",
    "industry_monthly_revenue.parquet",
    "industry_growth_summary.csv",
    "category_to_group_mapping.csv",
    "data_dictionary.csv",
    "metadata.json",
]
KNOWN_OUTPUT_DIRS = ["charts"]


def _assert_safe_output_dir(out):
    """Refuse an output path that would let --overwrite delete source code or inputs.

    Unsafe only if `out` itself IS the project root or a known input path, or is an
    ANCESTOR of one (deleting out's contents would then delete that input or the whole
    project) -- `out` simply living *under* the project root, as the default
    member3_industry_growth/results does, is normal and fine.
    """
    out = out.resolve()
    unsafe = {ROOT.resolve(), TRANSACTIONS.resolve(), MERCHANT_FEATURES.resolve().parent}
    if out == Path(out.anchor):
        raise SystemExit(f"Refusing to use {out} as --output: it is a filesystem root")
    for candidate in unsafe:
        if out == candidate or out in candidate.parents:
            raise SystemExit(f"Refusing to use {out} as --output: it is, or contains, a known input path or the project root ({candidate})")


def _safe_replace_output(build_dir, final_out):
    """Move only this module's own known files/dirs from build_dir into final_out, after
    the build has already fully succeeded (all assertions in run() passed before this is
    called). Never deletes anything in final_out this module doesn't itself own."""
    import shutil
    final_out.mkdir(parents=True, exist_ok=True)
    for name in KNOWN_OUTPUT_FILES:
        src_file = build_dir / name
        if not src_file.exists():
            continue
        dest_file = final_out / name
        if dest_file.exists():
            dest_file.unlink()
        shutil.move(str(src_file), str(dest_file))
    for name in KNOWN_OUTPUT_DIRS:
        src_dir = build_dir / name
        if not src_dir.exists():
            continue
        dest_dir = final_out / name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(src_dir), str(dest_dir))
    shutil.rmtree(build_dir, ignore_errors=True)


def run(output_dir, overwrite):
    final_out = output_dir
    _assert_safe_output_dir(final_out)
    if final_out.exists() and any(final_out.iterdir()):
        if not overwrite:
            raise SystemExit(f"{final_out} is not empty; pass --overwrite to replace an existing run")

    import shutil
    import tempfile
    build_dir = Path(tempfile.mkdtemp(prefix="industry_growth_build_", dir=str(final_out.parent) if final_out.parent.exists() else None))
    out = build_dir
    charts = out / "charts"
    out.mkdir(parents=True, exist_ok=True)
    charts.mkdir(parents=True, exist_ok=True)

    try:
        _run_pipeline(out, charts)
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    _safe_replace_output(build_dir, final_out)
    log(f"DONE -- output written to {final_out}")


def _run_pipeline(out, charts):
    log("loading merchant_features for category -> group mapping")
    merchants = pd.read_parquet(MERCHANT_FEATURES, columns=["merchant_abn", "merchant_category", "has_merchant_master_record"])
    categories = set(merchants["merchant_category"].dropna().unique())
    mapped = set(CATEGORY_TO_GROUP)
    if categories - mapped:
        raise ValueError(f"Categories missing from CATEGORY_TO_GROUP: {categories - mapped}")
    if mapped - categories:
        raise ValueError(f"CATEGORY_TO_GROUP has stale categories not seen in data: {mapped - categories}")
    merchants["industry_group"] = merchants["merchant_category"].map(CATEGORY_TO_GROUP).fillna("Unclassified")
    n_unclassified = (merchants["industry_group"] == "Unclassified").sum()
    if n_unclassified != (~merchants["has_merchant_master_record"]).sum():
        raise AssertionError("Unclassified count should exactly equal merchants with no master record")
    log(f"merchants={len(merchants)} unclassified={n_unclassified}")

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("SET memory_limit='3GB'")
    con.register("merchant_group", merchants[["merchant_abn", "industry_group"]])

    log("aggregating monthly revenue by industry group (all months, including partial ones)")
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW txn AS
        SELECT merchant_abn, order_datetime, order_year, order_month, dollar_value
        FROM read_parquet({json.dumps(str(TRANSACTIONS / '**/*.parquet'))}, hive_partitioning=true)
    """)

    coverage = con.execute("""
        SELECT order_year, order_month,
            count(DISTINCT day(order_datetime)) AS days_with_data,
            day(last_day(make_date(order_year, order_month, 1))) AS days_in_month
        FROM txn GROUP BY order_year, order_month ORDER BY 1, 2
    """).fetchdf()
    log("month day-coverage:\n" + coverage.to_string())
    partial_by_coverage = set(map(tuple, coverage[coverage["days_with_data"] < coverage["days_in_month"]][["order_year", "order_month"]].values.tolist()))
    expected_partial = {(2021, 2), (2022, 10)}
    if partial_by_coverage != expected_partial:
        raise AssertionError(
            f"Partial-month set changed since this window was fixed: found {partial_by_coverage}, expected {expected_partial}. "
            "Review WINDOW_START/WINDOW_END before proceeding."
        )

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE monthly AS
        SELECT g.industry_group, t.order_year, t.order_month,
               sum(t.dollar_value) AS revenue,
               count(*) AS transactions,
               count(DISTINCT t.merchant_abn) AS active_merchants,
               count(DISTINCT day(t.order_datetime)) AS days_with_data,
               (t.order_year * 12 + t.order_month) BETWEEN {WINDOW_START_IDX} AND {WINDOW_END_IDX} AS is_full_month
        FROM txn t JOIN merchant_group g USING (merchant_abn)
        GROUP BY g.industry_group, t.order_year, t.order_month
    """)
    monthly = con.execute("SELECT * FROM monthly ORDER BY industry_group, order_year, order_month").fetchdf()
    monthly["avg_daily_revenue"] = monthly["revenue"] / monthly["days_with_data"]
    log(f"monthly panel rows={len(monthly)} (all months; is_full_month flags the {WINDOW_START}..{WINDOW_END} window)")

    total_revenue_all = con.execute("SELECT sum(dollar_value) FROM txn").fetchone()[0]
    if abs(monthly["revenue"].sum() - total_revenue_all) > 0.01:
        raise AssertionError("Sum of grouped monthly revenue does not match transaction total")
    total_revenue_full = monthly.loc[monthly.is_full_month, "revenue"].sum()

    monthly["month_start"] = pd.to_datetime(dict(year=monthly.order_year, month=monthly.order_month, day=1))
    monthly = monthly.sort_values(["industry_group", "month_start"]).reset_index(drop=True)

    # Indexed revenue (base = 100 at each group's first FULL month). Computed for every
    # month (including the trailing partial one) so it can still be plotted for context,
    # but the base itself is always a full month.
    def index_group(g):
        base = g.loc[g.is_full_month, "revenue"].iloc[0]
        return g["revenue"] / base * 100

    monthly["revenue_index"] = monthly.groupby("industry_group", group_keys=False)[["revenue", "is_full_month"]].apply(index_group)

    log("computing per-group growth/stability summary over full months only (mirrors merchant_features)")
    full_monthly = monthly[monthly.is_full_month].copy()
    con.register("full_monthly_pd", full_monthly)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE group_growth AS
        SELECT industry_group,
            regr_slope(revenue, order_year*12+order_month) / nullif(regr_avgy(revenue, order_year*12+order_month), 0) AS normalized_monthly_revenue_trend,
            stddev_samp(revenue) / nullif(avg(revenue), 0) AS monthly_revenue_cv
        FROM full_monthly_pd
        GROUP BY industry_group
    """)
    summary = con.execute("""
        SELECT m.industry_group,
            sum(m.revenue) AS total_revenue,
            sum(m.transactions) AS total_transactions,
            max(m.active_merchants) AS max_active_merchants_in_a_month,
            gg.normalized_monthly_revenue_trend,
            gg.monthly_revenue_cv
        FROM full_monthly_pd m
        JOIN group_growth gg USING (industry_group)
        GROUP BY ALL
    """).fetchdf()
    summary["revenue_share"] = summary["total_revenue"] / summary["total_revenue"].sum()
    merchant_counts = merchants.groupby("industry_group")["merchant_abn"].nunique().rename("n_merchants")
    summary = summary.merge(merchant_counts, on="industry_group")
    summary = summary.sort_values("total_revenue", ascending=False).reset_index(drop=True)
    log("summary (full months only):\n" + summary.to_string())

    # ---------------------------------------------------------------------
    # Chart 1: indexed monthly revenue by group -- the main growth visual.
    # Full months: solid line. Trailing partial month (2022-10): separate marker,
    # not solid-line-connected, annotated as partial.
    # ---------------------------------------------------------------------
    log("drawing indexed growth chart")
    fig, ax = plt.subplots(figsize=(9.5, 6), dpi=150)
    last_full_date = pd.Timestamp(WINDOW_END[0], WINDOW_END[1], 1)
    for group in GROUP_ORDER:
        g = monthly[monthly.industry_group == group]
        if g.empty:
            continue
        full = g[g.is_full_month]
        partial = g[~g.is_full_month & (g.month_start > last_full_date)]
        ax.plot(full.month_start, full.revenue_index, label=group, color=GROUP_COLORS[group], linewidth=2)
        if len(partial):
            bridge = pd.concat([full.tail(1), partial])
            ax.plot(bridge.month_start, bridge.revenue_index, color=GROUP_COLORS[group], linewidth=1.5, linestyle="--")
            ax.plot(partial.month_start, partial.revenue_index, color=GROUP_COLORS[group], marker="o",
                    markerfacecolor="white", markeredgewidth=1.5, markersize=6, linestyle="none")
    ax.axhline(100, color="#B0B0B0", linewidth=1, linestyle="--", zorder=0)
    ax.annotate("2022-10: partial-month actual\n(observed data through the 26th only --\nnot a forecast/projection)",
                xy=(monthly.month_start.max(), monthly.loc[monthly.month_start.idxmax(), "revenue_index"]),
                xytext=(-170, -40), textcoords="offset points", fontsize=8, color="#404040",
                arrowprops=dict(arrowstyle="-", color="#909090", lw=0.8))
    ax.set_title(f"Sample-merchant revenue trend by industry group\n(indexed, {WINDOW_START[0]}-{WINDOW_START[1]:02d} = 100; this dataset only, not an economy-wide indicator)")
    ax.set_xlabel("Month")
    ax.set_ylabel("Revenue index (first full month = 100)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%d"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(charts / "industry_growth_indexed.png")
    plt.close(fig)

    # ---------------------------------------------------------------------
    # Chart 2: total revenue share by group, full months only (magnitude).
    # ---------------------------------------------------------------------
    log("drawing revenue-share chart")
    fig, ax = plt.subplots(figsize=(9.5, 4.5), dpi=150)
    order = summary.sort_values("total_revenue")
    bars = ax.barh(order.industry_group, order.total_revenue / 1e6, color=[GROUP_COLORS[g] for g in order.industry_group])
    for bar, share in zip(bars, order.revenue_share):
        ax.text(bar.get_width() + 2, bar.get_y() + bar.get_height() / 2, f"{share:.1%}", va="center", fontsize=9, color="#404040")
    ax.set_title(f"Total sample-merchant revenue by industry group\n(full calendar months only: {WINDOW_START[0]}-{WINDOW_START[1]:02d} to {WINDOW_END[0]}-{WINDOW_END[1]:02d})")
    ax.set_xlim(0, order.total_revenue.max() / 1e6 * 1.12)
    ax.set_xlabel("Total revenue (AUD, millions)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(charts / "industry_revenue_share.png")
    plt.close(fig)

    # ---------------------------------------------------------------------
    # Chart 3: Sep vs Oct average DAILY revenue by group -- the auxiliary check
    # for October's lower month-TOTAL: OBSERVED per-day average revenue is higher in
    # October than September for every group. This does not prove there was no business
    # decline (a complete October is not observed); it only shows the month-total figure
    # alone cannot be read as evidence of one.
    # ---------------------------------------------------------------------
    log("drawing Sep-vs-Oct daily-average chart")
    sep = monthly[(monthly.order_year == 2022) & (monthly.order_month == 9)].set_index("industry_group")["avg_daily_revenue"]
    oct_ = monthly[(monthly.order_year == 2022) & (monthly.order_month == 10)].set_index("industry_group")["avg_daily_revenue"]
    cmp_df = pd.DataFrame({"2022-09 (full month)": sep, "2022-10 (partial, through 26th)": oct_}).reindex(
        [g for g in GROUP_ORDER if g in sep.index]
    )
    fig, ax = plt.subplots(figsize=(9.5, 5), dpi=150)
    x = range(len(cmp_df))
    width = 0.36
    ax.bar([i - width / 2 for i in x], cmp_df["2022-09 (full month)"] / 1000, width=width, label="Sep 2022 (full month)", color="#0072B2")
    ax.bar([i + width / 2 for i in x], cmp_df["2022-10 (partial, through 26th)"] / 1000, width=width, label="Oct 2022 (through the 26th)", color="#E69F00")
    ax.set_xticks(list(x))
    ax.set_xticklabels(cmp_df.index, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Average daily revenue (AUD, thousands)")
    ax.set_title("Average DAILY revenue is higher Sep -> Oct even though the Oct MONTH TOTAL is lower\n(October's total reflects incomplete observation and should not be read alone as a business decline)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(charts / "industry_sep_vs_oct_daily_avg.png")
    plt.close(fig)
    pct_change = (oct_ - sep) / sep
    log("Sep->Oct daily-avg % change by group:\n" + pct_change.to_string())

    # ---------------------------------------------------------------------
    # Write data outputs.
    # ---------------------------------------------------------------------
    log("writing outputs")
    monthly_out = monthly[["industry_group", "order_year", "order_month", "revenue", "transactions",
                            "active_merchants", "days_with_data", "avg_daily_revenue", "is_full_month", "revenue_index"]]
    monthly_out.to_csv(out / "industry_monthly_revenue.csv", index=False)
    monthly_out.to_parquet(out / "industry_monthly_revenue.parquet", index=False)
    summary.to_csv(out / "industry_growth_summary.csv", index=False)

    mapping_rows = [{"merchant_category": k, "industry_group": v} for k, v in sorted(CATEGORY_TO_GROUP.items())]
    pd.DataFrame(mapping_rows).to_csv(out / "category_to_group_mapping.csv", index=False)

    data_dictionary = [
        {"field": "industry_group", "definition": "One of 5 business-defined industry groups (or 'Unclassified' for merchants with no tbl_merchants record, kept for audit only, not a 6th official segment); see category_to_group_mapping.csv", "unit": "category"},
        {"field": "order_year", "definition": "Calendar year of the transaction month", "unit": "year"},
        {"field": "order_month", "definition": "Calendar month of the transaction month", "unit": "month 1-12"},
        {"field": "revenue", "definition": "Sum of dollar_value across curated transactions for this group and month (this sample's merchants only)", "unit": "AUD"},
        {"field": "transactions", "definition": "Count of curated transactions for this group and month", "unit": "count"},
        {"field": "active_merchants", "definition": "Distinct merchant_abn with at least one transaction in this group and month", "unit": "count"},
        {"field": "days_with_data", "definition": "Distinct calendar days with at least one transaction in this group and month", "unit": "count"},
        {"field": "avg_daily_revenue", "definition": "revenue / days_with_data; comparable across a partial and a full month, unlike the raw monthly total", "unit": "AUD/day"},
        {"field": "is_full_month", "definition": f"True for the {WINDOW_START[0]}-{WINDOW_START[1]:02d} .. {WINDOW_END[0]}-{WINDOW_END[1]:02d} window (every day present); False for 2021-02 (1/28 days) and 2022-10 (26/31 days). Growth/CV/revenue-share are computed on True rows only", "unit": "boolean"},
        {"field": "revenue_index", "definition": "revenue / (this group's revenue in its first FULL month) * 100; lets groups of very different scale be compared on one axis. Computed for every month for context, but the base is always a full month", "unit": "index, first full month = 100"},
        {"field": "total_revenue", "definition": "Sum of revenue across full months only for this group", "unit": "AUD"},
        {"field": "revenue_share", "definition": "total_revenue / total_revenue across all groups (full months only)", "unit": "proportion 0-1"},
        {"field": "normalized_monthly_revenue_trend", "definition": "Linear trend slope of monthly group revenue vs. month, divided by mean monthly group revenue (regr_slope/regr_avgy), over full months only; NOT a month-over-month growth rate", "unit": "proportion of mean revenue, per month"},
        {"field": "monthly_revenue_cv", "definition": "Coefficient of variation (stddev/mean) of monthly group revenue, full months only", "unit": "ratio"},
        {"field": "n_merchants", "definition": "Distinct merchants in this industry group (from merchant_features)", "unit": "count"},
    ]
    pd.DataFrame(data_dictionary).to_csv(out / "data_dictionary.csv", index=False)

    metadata = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "input_transactions": str(TRANSACTIONS),
        "input_merchant_features": str(MERCHANT_FEATURES),
        "n_categories": len(CATEGORY_TO_GROUP),
        "n_groups": len(GROUP_ORDER),
        "n_unclassified_merchants": int(n_unclassified),
        "full_month_window": {"start": f"{WINDOW_START[0]}-{WINDOW_START[1]:02d}", "end": f"{WINDOW_END[0]}-{WINDOW_END[1]:02d}"},
        "partial_months": sorted(list(expected_partial)),
        "sep_to_oct_2022_daily_avg_pct_change_by_group": pct_change.to_dict(),
        "total_revenue_all_months": str(total_revenue_all),
        "total_revenue_full_months_only": str(total_revenue_full),
        "grouping_method": "Manual grouping of 25 MCC-style categories into 5 industry groups, confirmed with project owner; see category_to_group_mapping.csv",
        "scope_caveat": "All figures describe this dataset's sample merchants only; they are not a claim about the whole Australian retail industry.",
        "versions": {"duckdb": duckdb.__version__, "pandas": pd.__version__, "matplotlib": matplotlib.__version__},
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    con.close()
    log("pipeline build complete (still in temp build dir, about to be moved into place)")
    print(json.dumps(metadata, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory (default: member3_industry_growth/results)")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing non-empty output directory instead of refusing to run")
    args = parser.parse_args()
    run(args.output.resolve(), args.overwrite)
