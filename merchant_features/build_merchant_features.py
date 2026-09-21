"""Build the merchant-level feature table for the BNPL merchant-selection project (step 3).

One row per merchant_abn, covering:
  - transaction scale / recency (total_transactions, total_revenue, active_months, date range)
  - average order value (avg_transaction_value, 客单价)
  - repeat custom (repeat_consumer_share, 复购)
  - revenue trend (normalized_monthly_revenue_trend, 增长) and stability (monthly_revenue_cv,
    稳定性), computed over a fixed full-calendar-month window with missing months zero-filled
  - customer-base regional/socio-economic profile (transaction-weighted averages of a curated,
    non-redundant subset of census/seifa/ato postcode-level features, plus per-source coverage)
  - merchant category / pricing level / take rate (parsed from tbl_merchants.tags)
  - estimated_bnpl_revenue = total_revenue * take_rate_pct / 100

Revision history / review fixes (see VALIDATION.md for the full writeup):
  - Growth/stability used to include the two partial calendar months (2021-02, only 1 of 28
    days present; 2022-10, only 26 of 31 days) and silently skipped months with zero
    transactions rather than counting them as zero revenue. Both distort a linear-trend slope:
    a reviewer re-ran the same formula over a fixed full-month window (2021-03 to 2022-09) and
    found the growth direction flipped sign for 253 of 4,405 merchants, and a top-100-by-growth
    selection only overlapped 74/100 between the two versions. Fixed here by (a) restricting to
    WINDOW_START..WINDOW_END (both full calendar months for every day of data) and (b) building
    an explicit monthly calendar per merchant, from their first to last transaction *within that
    window*, with missing months filled as zero revenue (never inventing months before a
    merchant's first transaction).
  - The growth field is a normalised trend slope (regr_slope / regr_avgy), not a month-over-month
    growth rate. Renamed monthly_revenue_growth_rate -> normalized_monthly_revenue_trend so it
    isn't misread as a simple MoM/YoY percentage.
  - The original 16-field external subset had several near-duplicate pairs (e.g. regional
    population vs. total-family count, r~0.99; a SEIFA index's score vs. its own decile,
    r~0.97). Family-structure counts are now expressed as shares of total families
    (removing the population-scale redundancy) and each SEIFA index keeps only its national
    decile (dropping the raw score, which is a near-linear transform of the same decile at
    this level of precision).
  - Coverage was previously a single all-three-sources figure. Census, SEIFA and ATO are matched
    independently (same postcode join, three separate source flags), so coverage is now reported
    per source as well as for all three combined.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

t0 = time.time()


def log(msg):
    print(f"{time.time()-t0:.2f}s {msg}", flush=True)


ROOT = Path(__file__).resolve().parent.parent
TRANSACTIONS = ROOT / "member2_curation/data/curated/curated_transactions"
MERCHANTS_PARQUET = ROOT / "tables/tbl_merchants.parquet"
DEFAULT_OUTPUT = ROOT / "merchant_features/results"

sys.path.insert(0, str(ROOT / "external_integration"))
from integrate import SOURCES, build_dimension  # noqa: E402

# ---------------------------------------------------------------------------
# Fixed full-calendar-month window used for the growth/stability calculation.
# curated_transactions spans 2021-02-28 .. 2022-10-26; 2021-02 (1/28 days) and
# 2022-10 (26/31 days) are excluded because they are not full months -- see
# module docstring and VALIDATION.md.
# ---------------------------------------------------------------------------
WINDOW_START = (2021, 3)
WINDOW_END = (2022, 9)
WINDOW_START_IDX = WINDOW_START[0] * 12 + WINDOW_START[1]
WINDOW_END_IDX = WINDOW_END[0] * 12 + WINDOW_END[1]

# ---------------------------------------------------------------------------
# Low-sample thresholds for the trend/CV estimate. A review found the original
# single "potential_months_in_window < 3" check missed merchants with very few
# actual transactions or very few active months whenever their calendar span
# happened to be long (e.g. one merchant with only 2 transactions passed
# because its first/last transaction were 14 months apart). Replaced with
# three independent checks, each of which can flag a merchant on its own.
# These thresholds are this team's own judgment call, not a statistical or
# industry standard -- see VALIDATION.md for a comparison of how alternate
# thresholds would change which merchants get flagged.
# ---------------------------------------------------------------------------
MIN_MONTHS_FOR_GROWTH = 3       # potential_months_in_window below this -> low_sample_short_window
MIN_TRANSACTIONS_FOR_GROWTH = 10  # transactions_in_window below this -> low_sample_few_transactions
MIN_ACTIVE_MONTHS_FOR_GROWTH = 3  # active_months_in_window below this -> low_sample_few_active_months

# ---------------------------------------------------------------------------
# Curated, non-redundant subset of external postcode features (13 fields).
# Family-structure fields are expressed as shares of total families (not raw counts,
# which are ~collinear with population); each SEIFA index keeps only its national
# decile (score dropped -- collinear with the decile at this granularity).
# ---------------------------------------------------------------------------
SELECTED_EXTERNAL_FEATURES = [
    "census_population",
    "census_median_household_income_weekly",
    "census_avg_household_size",
    "census_single_parent_family_share",
    "census_couple_with_children_family_share",
    "census_unemployment_rate_published_pct",
    "census_labour_force_participation_rate_published_pct",
    "seifa_irsd_national_decile",
    "seifa_irsad_national_decile",
    "seifa_ier_national_decile",
    "ato_taxable_income_or_loss_per_reporter",
    "ato_salary_or_wages_per_recipient",
    "ato_net_tax_payer_share",
]

FEATURE_SOURCE = {
    "census_population": "census",
    "census_median_household_income_weekly": "census",
    "census_avg_household_size": "census",
    "census_single_parent_family_share": "census",
    "census_couple_with_children_family_share": "census",
    "census_unemployment_rate_published_pct": "census",
    "census_labour_force_participation_rate_published_pct": "census",
    "seifa_irsd_national_decile": "seifa",
    "seifa_irsad_national_decile": "seifa",
    "seifa_ier_national_decile": "seifa",
    "ato_taxable_income_or_loss_per_reporter": "ato",
    "ato_salary_or_wages_per_recipient": "ato",
    "ato_net_tax_payer_share": "ato",
}

FEATURE_DEFINITIONS = {
    "census_population": ("Total usual residents in the merchant's customer postcodes (transaction-weighted average)", "count"),
    "census_median_household_income_weekly": ("Median weekly household income of customer postcodes (transaction-weighted average)", "AUD/week"),
    "census_avg_household_size": ("Average household size of customer postcodes (transaction-weighted average)", "persons/household"),
    "census_single_parent_family_share": ("One-parent families / total families in customer postcodes (transaction-weighted average); replaces the raw one-parent-family count to avoid double-counting regional population scale", "proportion 0-1"),
    "census_couple_with_children_family_share": ("Couple-with-children families / total families in customer postcodes (transaction-weighted average); replaces the raw count for the same reason", "proportion 0-1"),
    "census_unemployment_rate_published_pct": ("ABS-published unemployment rate of customer postcodes (transaction-weighted average)", "percentage points"),
    "census_labour_force_participation_rate_published_pct": ("ABS-published labour-force participation rate of customer postcodes (transaction-weighted average)", "percentage points"),
    "seifa_irsd_national_decile": ("IRSD (disadvantage) national decile of customer postcodes; 1=most disadvantaged, 10=least (transaction-weighted average). The raw score is dropped: it is a near-linear transform of the decile at postcode granularity", "integer 1-10 (avg)"),
    "seifa_irsad_national_decile": ("IRSAD (advantage/disadvantage) national decile of customer postcodes (transaction-weighted average); score dropped for the same reason", "integer 1-10 (avg)"),
    "seifa_ier_national_decile": ("IER (economic resources) national decile of customer postcodes (transaction-weighted average); score dropped for the same reason", "integer 1-10 (avg)"),
    "ato_taxable_income_or_loss_per_reporter": ("Average taxable income per reporter (2021-22) in customer postcodes (transaction-weighted average)", "AUD/year"),
    "ato_salary_or_wages_per_recipient": ("Average salary/wages per recipient (2021-22) in customer postcodes (transaction-weighted average)", "AUD/year"),
    "ato_net_tax_payer_share": ("Share of individuals paying net tax (2021-22) in customer postcodes (transaction-weighted average)", "proportion 0-1"),
}

TAG_RE = re.compile(
    r"^[\(\[]{1,2}(.*?)[\)\]]{1,2},\s*[\(\[]([a-eA-E])[\)\]],\s*[\(\[]take rate:\s*([0-9.]+)[\)\]]{1,2}$"
)


def parse_tags(tag):
    if tag is None:
        return None, None, None
    m = TAG_RE.match(tag.strip())
    if not m:
        return None, None, None
    # Lower-case: the same category appears with inconsistent capitalisation across rows
    # (e.g. "Telecom" vs "telecom"); collapsing case is what actually yields the project's
    # 25 categories -- whitespace normalisation alone still leaves ~847 case-variant labels.
    category = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    level = m.group(2).upper()
    take_rate = float(m.group(3))
    return category, level, take_rate


def quote(c):
    return '"' + c.replace('"', '""') + '"'


def build_reduced_dimension(paths):
    """External dimension reduced to the selected columns, with family-structure raw
    counts converted to shares of total families before the raw counts are dropped."""
    dimension, dictionary, sources_meta = build_dimension(paths)
    dimension = dimension.copy()
    dimension["census_single_parent_family_share"] = (
        dimension["census_single_parent_families"] / dimension["census_total_families"].replace(0, pd.NA)
    )
    dimension["census_couple_with_children_family_share"] = (
        dimension["census_couple_with_children_families"] / dimension["census_total_families"].replace(0, pd.NA)
    )
    keep = ["postcode"] + [f"{s}_matched" for s in SOURCES] + ["all_sources_matched", "ato_small_denominator"] + SELECTED_EXTERNAL_FEATURES
    missing = [c for c in keep if c not in dimension.columns]
    if missing:
        raise ValueError(f"Selected features missing from dimension: {missing}")
    return dimension[keep].copy(), sources_meta


# Files this module is specifically known to generate directly under the output directory.
# --overwrite only ever touches these names (plus local_data/, rebuilt fresh each run) --
# never a blanket rmtree of the whole output directory. A review found the previous
# --overwrite deleted broadly and before any input validation; fixed by (a) refusing
# unsafe output paths outright, and (b) building the whole run in a temp directory first
# and only replacing the real output directory's known files once that temp run's own
# validation (row-count / revenue-reconciliation assertions, above) has already passed.
KNOWN_OUTPUT_FILES = [
    "merchant_features.parquet",
    "merchant_features.csv",
    "data_dictionary.csv",
    "metadata.json",
    "low_sample_threshold_comparison.csv",
    "feature_nonnull_rate_when_matched.csv",
]


def _assert_safe_output_dir(out):
    """Refuse an output path that would let --overwrite delete source code or inputs.

    The danger is deleting FILES INSIDE `out`: that's fine when `out` is an ordinary
    subdirectory this module owns (e.g. the default merchant_features/results, which
    naturally lives *under* the project root -- that nesting is expected and safe), but
    unsafe if `out` itself IS the project root or an input directory, or is an ANCESTOR of
    one (deleting out's contents would then delete that input or the whole project).
    """
    out = out.resolve()
    unsafe = {ROOT.resolve(), MERCHANTS_PARQUET.resolve().parent, TRANSACTIONS.resolve()}
    unsafe |= {(ROOT / f"external_{s}/results").resolve() for s in SOURCES}
    if out == Path(out.anchor):
        raise SystemExit(f"Refusing to use {out} as --output: it is a filesystem root")
    for candidate in unsafe:
        if out == candidate or out in candidate.parents:
            raise SystemExit(f"Refusing to use {out} as --output: it is, or contains, a known input directory or the project root ({candidate})")


def _safe_replace_output(build_dir, final_out):
    """Move only this module's own known files from build_dir into final_out, after the
    build has already fully succeeded (all assertions in run() passed before this is
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
    # local_data/ holds only transient duckdb spill + a .gitkeep; safe to replace wholesale
    # since this module owns that specific subdirectory and nothing else lives in it.
    final_local = final_out / "local_data"
    if final_local.exists():
        shutil.rmtree(final_local)
    build_local = build_dir / "local_data"
    if build_local.exists():
        shutil.move(str(build_local), str(final_local))
    shutil.rmtree(build_dir, ignore_errors=True)


def run(output_dir, overwrite):
    final_out = output_dir
    _assert_safe_output_dir(final_out)
    if final_out.exists() and any(p for p in final_out.iterdir()):
        if not overwrite:
            raise SystemExit(f"{final_out} is not empty; pass --overwrite to replace an existing run")

    import shutil
    import tempfile
    build_dir = Path(tempfile.mkdtemp(prefix="merchant_features_build_", dir=str(final_out.parent) if final_out.parent.exists() else None))
    out = build_dir  # everything below writes into the temp build dir until the final move
    local = out / "local_data"
    out.mkdir(parents=True, exist_ok=True)
    local.mkdir(parents=True, exist_ok=True)

    try:
        _run_pipeline(out, local)
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    _safe_replace_output(build_dir, final_out)
    log(f"DONE -- output written to {final_out}")


def _run_pipeline(out, local):
    log("building external dimension (reduced to 13 selected features)")
    paths = {s: ROOT / f"external_{s}/results/{s}_clean.parquet" for s in SOURCES}
    reduced_dimension, sources_meta = build_reduced_dimension(paths)
    log(f"reduced_dimension rows={len(reduced_dimension)} cols={len(reduced_dimension.columns)}")

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("SET memory_limit='3GB'")
    con.execute(f"SET temp_directory={json.dumps(str(local / 'duckdb_spill'))}")
    con.register("external_dimension", reduced_dimension)

    log("reading curated_transactions and joining external dimension")
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW txn AS
        SELECT *,
            CASE WHEN regexp_full_match(trim(CAST(consumer_postcode AS VARCHAR)), '[0-9]{{1,4}}')
                 THEN lpad(trim(CAST(consumer_postcode AS VARCHAR)), 4, '0') ELSE NULL END AS external_postcode
        FROM read_parquet({json.dumps(str(TRANSACTIONS / '**/*.parquet'))}, hive_partitioning=true)
    """)
    feature_cols = ", ".join(f"d.{quote(c)}" for c in SELECTED_EXTERNAL_FEATURES)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW txn_enriched AS
        SELECT t.order_id, t.order_datetime, t.order_year, t.order_month, t.merchant_abn,
               t.dollar_value, t.consumer_id, t.consumer_postcode,
               coalesce(d.census_matched, false) AS census_matched,
               coalesce(d.seifa_matched, false) AS seifa_matched,
               coalesce(d.ato_matched, false) AS ato_matched,
               coalesce(d.all_sources_matched, false) AS regional_data_matched,
               d.ato_small_denominator,
               {feature_cols}
        FROM txn t LEFT JOIN external_dimension d ON t.external_postcode = d.postcode
    """)
    check = con.execute("SELECT count(*), count(DISTINCT order_id) FROM txn_enriched").fetchone()
    log(f"txn_enriched rows={check}")

    log("aggregating transaction-level merchant metrics (incl. per-source coverage)")
    weighted_avgs = ", ".join(
        f"sum({quote(c)}) FILTER (WHERE {quote(c)} IS NOT NULL) / "
        f"nullif(count(*) FILTER (WHERE {quote(c)} IS NOT NULL), 0) AS {quote('avg_'+c)}"
        for c in SELECTED_EXTERNAL_FEATURES
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merchant_txn_agg AS
        SELECT
            merchant_abn,
            count(*) AS total_transactions,
            sum(dollar_value) AS total_revenue,
            avg(dollar_value) AS avg_transaction_value,
            count(DISTINCT consumer_id) AS unique_consumers,
            min(order_datetime) AS first_order_date,
            max(order_datetime) AS last_order_date,
            count(DISTINCT (order_year, order_month)) AS active_months_all_time,
            sum(CASE WHEN regional_data_matched THEN dollar_value ELSE 0 END) / nullif(sum(dollar_value), 0) AS regional_data_coverage_rate_all_sources,
            sum(CASE WHEN census_matched THEN dollar_value ELSE 0 END) / nullif(sum(dollar_value), 0) AS census_data_coverage_rate,
            sum(CASE WHEN seifa_matched THEN dollar_value ELSE 0 END) / nullif(sum(dollar_value), 0) AS seifa_data_coverage_rate,
            sum(CASE WHEN ato_matched THEN dollar_value ELSE 0 END) / nullif(sum(dollar_value), 0) AS ato_data_coverage_rate,
            -- Row-count-weighted coverage, added after a review found the amount-weighted
            -- figure above can look far higher than actual data richness: the regional
            -- feature AVERAGES themselves (weighted_avgs, below) are row-count weighted, so
            -- a coverage figure meant to describe THEIR reliability should use the same
            -- weight. Measured gap for individual merchants (ATO): up to 41.35 points.
            count(*) FILTER (WHERE regional_data_matched)::DOUBLE / nullif(count(*), 0) AS regional_data_coverage_rate_all_sources_by_count,
            count(*) FILTER (WHERE census_matched)::DOUBLE / nullif(count(*), 0) AS census_data_coverage_rate_by_count,
            count(*) FILTER (WHERE seifa_matched)::DOUBLE / nullif(count(*), 0) AS seifa_data_coverage_rate_by_count,
            count(*) FILTER (WHERE ato_matched)::DOUBLE / nullif(count(*), 0) AS ato_data_coverage_rate_by_count,
            -- Share of this merchant's ATO-matched transactions whose postcode's ATO ratios
            -- rest on fewer than 100 individuals (external_ato's own ato_small_denominator
            -- flag) -- surfaces the ATO small-denominator data-quality issue at merchant level.
            count(*) FILTER (WHERE ato_matched AND ato_small_denominator)::DOUBLE / nullif(count(*) FILTER (WHERE ato_matched), 0) AS ato_small_denominator_share_of_matched,
            {weighted_avgs}
        FROM txn_enriched
        GROUP BY merchant_abn
    """)
    log("merchant_txn_agg done: " + str(con.execute("SELECT count(*) FROM merchant_txn_agg").fetchone()))

    log("computing repeat-consumer share")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_consumer_counts AS
        SELECT merchant_abn, consumer_id, count(*) AS orders
        FROM txn_enriched GROUP BY merchant_abn, consumer_id
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_repeat AS
        SELECT merchant_abn,
            count(*) FILTER (WHERE orders > 1)::DOUBLE / nullif(count(*), 0) AS repeat_consumer_share
        FROM merchant_consumer_counts GROUP BY merchant_abn
    """)
    log("merchant_repeat done")

    # ------------------------------------------------------------------
    # Revenue trend / stability: fixed full-month window, zero-filled calendar
    # per merchant (from the merchant's first to last transaction *within the
    # window*, not before their first ever transaction).
    # ------------------------------------------------------------------
    log("computing revenue trend/stability over the fixed full-month window (zero-filled)")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE txn_window AS
        SELECT * FROM txn_enriched
        WHERE (order_year * 12 + order_month) BETWEEN {WINDOW_START_IDX} AND {WINDOW_END_IDX}
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_monthly_window AS
        SELECT merchant_abn, order_year * 12 + order_month AS month_index, sum(dollar_value) AS monthly_revenue
        FROM txn_window GROUP BY merchant_abn, order_year, order_month
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merchant_window_bounds AS
        SELECT merchant_abn,
            greatest(year(first_order_date) * 12 + month(first_order_date), {WINDOW_START_IDX}) AS start_idx,
            least(year(last_order_date) * 12 + month(last_order_date), {WINDOW_END_IDX}) AS end_idx
        FROM merchant_txn_agg
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_months AS
        SELECT merchant_abn, unnest(generate_series(start_idx, end_idx)) AS month_index, (end_idx - start_idx + 1) AS potential_months_in_window
        FROM merchant_window_bounds
        WHERE start_idx <= end_idx
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_monthly_filled AS
        SELECT mm.merchant_abn, mm.month_index, mm.potential_months_in_window,
               coalesce(w.monthly_revenue, 0) AS monthly_revenue
        FROM merchant_months mm
        LEFT JOIN merchant_monthly_window w USING (merchant_abn, month_index)
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merchant_growth AS
        SELECT merchant_abn,
            max(potential_months_in_window) AS potential_months_in_window,
            count(*) FILTER (WHERE monthly_revenue > 0) AS active_months_in_window,
            regr_slope(monthly_revenue, month_index) / nullif(regr_avgy(monthly_revenue, month_index), 0) AS normalized_monthly_revenue_trend,
            stddev_samp(monthly_revenue) / nullif(avg(monthly_revenue), 0) AS monthly_revenue_cv,
            max(potential_months_in_window) < {MIN_MONTHS_FOR_GROWTH} AS low_sample_short_window,
            count(*) FILTER (WHERE monthly_revenue > 0) < {MIN_ACTIVE_MONTHS_FOR_GROWTH} AS low_sample_few_active_months
        FROM merchant_monthly_filled
        GROUP BY merchant_abn
    """)
    log("merchant_growth done: " + str(con.execute("SELECT count(*) FROM merchant_growth").fetchone()))

    # A review found low_sample_few_transactions used total_transactions (all-time), but
    # the growth/CV estimate only ever looks at the fixed window -- 18 merchants had only
    # 8-9 transactions IN the window but 10-12 all-time, so they weren't flagged even
    # though the growth estimate itself is thin. transactions_in_window is the correct
    # basis for that flag; total_transactions (all-time) is kept separately as a scale
    # metric, unrelated to the growth-window analysis.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE merchant_txn_count_window AS
        SELECT merchant_abn, count(*) AS transactions_in_window
        FROM txn_window GROUP BY merchant_abn
    """)
    log("merchant_txn_count_window done: " + str(con.execute("SELECT count(*) FROM merchant_txn_count_window").fetchone()))

    log("parsing tbl_merchants tags")
    merchants_raw = pd.read_parquet(MERCHANTS_PARQUET).reset_index()
    if "merchant_abn" not in merchants_raw.columns:
        raise ValueError("tbl_merchants.parquet: expected a merchant_abn column or index")
    parsed = merchants_raw["tags"].map(parse_tags)
    merchants = pd.DataFrame({
        "merchant_abn": merchants_raw["merchant_abn"].astype(str).str.strip(),
        "merchant_name": merchants_raw["name"],
        "merchant_category": [p[0] for p in parsed],
        "merchant_pricing_level": [p[1] for p in parsed],
        "merchant_take_rate_pct": [p[2] for p in parsed],
    })
    if merchants["merchant_category"].isna().any():
        raise ValueError("tag parsing produced nulls; regex needs review")
    n_categories = merchants["merchant_category"].nunique()
    log(f"tbl_merchants rows={len(merchants)} distinct_categories={n_categories}")
    if n_categories != 25:
        raise AssertionError(f"Expected the project's 25 original merchant categories after normalisation, got {n_categories}")
    con.register("merchants", merchants)

    log("assembling merchant_features")
    avg_cols = ", ".join(f"a.{quote('avg_'+c)} AS {quote(c)}" for c in SELECTED_EXTERNAL_FEATURES)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merchant_features AS
        SELECT
            a.merchant_abn,
            m.merchant_name,
            m.merchant_category,
            m.merchant_pricing_level,
            m.merchant_take_rate_pct,
            m.merchant_abn IS NOT NULL AS has_merchant_master_record,
            a.total_transactions,
            a.total_revenue,
            a.avg_transaction_value,
            a.unique_consumers,
            r.repeat_consumer_share,
            a.first_order_date,
            a.last_order_date,
            a.active_months_all_time,
            g.potential_months_in_window,
            g.active_months_in_window,
            coalesce(twc.transactions_in_window, 0) AS transactions_in_window,
            g.normalized_monthly_revenue_trend,
            g.monthly_revenue_cv,
            coalesce(g.low_sample_short_window, true) AS low_sample_short_window,
            coalesce(twc.transactions_in_window, 0) < {MIN_TRANSACTIONS_FOR_GROWTH} AS low_sample_few_transactions,
            coalesce(g.low_sample_few_active_months, true) AS low_sample_few_active_months,
            (coalesce(g.low_sample_short_window, true)
                OR coalesce(twc.transactions_in_window, 0) < {MIN_TRANSACTIONS_FOR_GROWTH}
                OR coalesce(g.low_sample_few_active_months, true)) AS low_sample_growth_estimate,
            a.total_revenue * m.merchant_take_rate_pct / 100.0 AS estimated_bnpl_revenue,
            a.regional_data_coverage_rate_all_sources,
            a.census_data_coverage_rate,
            a.seifa_data_coverage_rate,
            a.ato_data_coverage_rate,
            a.regional_data_coverage_rate_all_sources_by_count,
            a.census_data_coverage_rate_by_count,
            a.seifa_data_coverage_rate_by_count,
            a.ato_data_coverage_rate_by_count,
            a.ato_small_denominator_share_of_matched,
            {avg_cols}
        FROM merchant_txn_agg a
        LEFT JOIN merchants m USING (merchant_abn)
        LEFT JOIN merchant_repeat r USING (merchant_abn)
        LEFT JOIN merchant_growth g USING (merchant_abn)
        LEFT JOIN merchant_txn_count_window twc USING (merchant_abn)
        ORDER BY a.merchant_abn
    """)
    n_rows = con.execute("SELECT count(*) FROM merchant_features").fetchone()[0]
    n_with_master = con.execute("SELECT count(*) FROM merchant_features WHERE has_merchant_master_record").fetchone()[0]
    n_without_master = n_rows - n_with_master
    n_low_sample = con.execute("SELECT count(*) FROM merchant_features WHERE low_sample_growth_estimate").fetchone()[0]
    n_low_sample_short_window = con.execute("SELECT count(*) FROM merchant_features WHERE low_sample_short_window").fetchone()[0]
    n_low_sample_few_txn = con.execute("SELECT count(*) FROM merchant_features WHERE low_sample_few_transactions").fetchone()[0]
    n_low_sample_few_active = con.execute("SELECT count(*) FROM merchant_features WHERE low_sample_few_active_months").fetchone()[0]
    log(f"merchant_features rows={n_rows} with_master={n_with_master} without_master={n_without_master} "
        f"low_sample_any={n_low_sample} (short_window={n_low_sample_short_window}, "
        f"few_transactions={n_low_sample_few_txn}, few_active_months={n_low_sample_few_active})")

    # Threshold-sensitivity comparison requested by review: how many merchants would each
    # flag catch under a few alternate threshold choices. Documents that the chosen
    # thresholds are a judgment call, not a derived constant.
    threshold_rows = []
    for months in (2, 3, 4, 6):
        n = con.execute(f"SELECT count(*) FROM merchant_features WHERE coalesce(potential_months_in_window, 0) < {months}").fetchone()[0]
        threshold_rows.append({"flag": "low_sample_short_window", "threshold": months, "merchants_flagged": n})
    for txns in (5, 10, 20, 50):
        n = con.execute(f"SELECT count(*) FROM merchant_features WHERE transactions_in_window < {txns}").fetchone()[0]
        threshold_rows.append({"flag": "low_sample_few_transactions", "threshold": txns, "merchants_flagged": n})
    for months in (2, 3, 4, 6):
        n = con.execute(f"SELECT count(*) FROM merchant_features WHERE coalesce(active_months_in_window, 0) < {months}").fetchone()[0]
        threshold_rows.append({"flag": "low_sample_few_active_months", "threshold": months, "merchants_flagged": n})
    pd.DataFrame(threshold_rows).to_csv(out / "low_sample_threshold_comparison.csv", index=False)

    # Non-null rate of derived fields *conditional on the source being matched*, requested
    # by review: "postcode matched" should not be silently equated with "field usable" --
    # some ATO fields in particular can be null even when ato_matched is true (e.g. a
    # suppressed/derived ratio). Computed at the transaction level (txn_enriched), not the
    # merchant level, since that's where the matched flag and the feature value both live.
    nonnull_rows = []
    for feat in SELECTED_EXTERNAL_FEATURES:
        source = FEATURE_SOURCE[feat]
        matched_col = f"{source}_matched"
        n_matched, n_matched_nonnull = con.execute(f"""
            SELECT count(*) FILTER (WHERE {quote(matched_col)}),
                   count(*) FILTER (WHERE {quote(matched_col)} AND {quote(feat)} IS NOT NULL)
            FROM txn_enriched
        """).fetchone()
        nonnull_rows.append({
            "field": feat,
            "source": source,
            "transactions_with_source_matched": n_matched,
            "transactions_with_source_matched_and_field_nonnull": n_matched_nonnull,
            "nonnull_rate_when_matched": (n_matched_nonnull / n_matched) if n_matched else None,
        })
    nonnull_df = pd.DataFrame(nonnull_rows)
    nonnull_df.to_csv(out / "feature_nonnull_rate_when_matched.csv", index=False)
    min_nonnull_rate = nonnull_df["nonnull_rate_when_matched"].min()
    log(f"lowest feature nonnull-rate-when-matched: {min_nonnull_rate:.4f}" if pd.notna(min_nonnull_rate) else "nonnull-rate check: no matched rows")

    if n_rows < 4000 or n_rows > 5000:
        raise AssertionError(f"Unexpected merchant row count: {n_rows}")

    total_revenue_check = con.execute("SELECT sum(total_revenue) FROM merchant_features").fetchone()[0]
    total_revenue_txn = con.execute("SELECT sum(dollar_value) FROM txn_enriched").fetchone()[0]
    if abs(total_revenue_check - total_revenue_txn) > 0.01:
        raise AssertionError("Sum of merchant total_revenue does not match transaction total")

    log("writing outputs")
    con.execute(f"COPY merchant_features TO {json.dumps(str(out / 'merchant_features.parquet'))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"COPY merchant_features TO {json.dumps(str(out / 'merchant_features.csv'))} (HEADER, DELIMITER ',')")

    core_fields = {
        "merchant_abn": ("Australian Business Number of the merchant; primary key", "id"),
        "merchant_name": ("Merchant trading name, from tbl_merchants", "string"),
        "merchant_category": ("One of 25 categories parsed from tbl_merchants.tags", "category"),
        "merchant_pricing_level": ("Pricing/risk tier parsed from tbl_merchants.tags, A (lowest) to E (highest)", "category A-E"),
        "merchant_take_rate_pct": ("BNPL take rate parsed from tbl_merchants.tags", "percent"),
        "has_merchant_master_record": ("False for merchant_abn seen in transactions but absent from tbl_merchants (396 of 4,422); category/pricing/take rate are null in that case. Kept for audit; whether these should be eligible for the final recommendation list is a separate policy decision", "boolean"),
        "total_transactions": ("Count of curated transactions for this merchant, all time (交易规模)", "count"),
        "total_revenue": ("Sum of dollar_value across this merchant's curated transactions, all time (交易规模)", "AUD"),
        "avg_transaction_value": ("total_revenue / total_transactions, all time (客单价)", "AUD"),
        "unique_consumers": ("Distinct consumer_id count for this merchant, all time", "count"),
        "repeat_consumer_share": ("Share of this merchant's consumers with more than one transaction, all time (复购)", "proportion 0-1"),
        "first_order_date": ("Earliest curated transaction date for this merchant", "date"),
        "last_order_date": ("Latest curated transaction date for this merchant", "date"),
        "active_months_all_time": ("Distinct (order_year, order_month) buckets with >=1 transaction, all time including the two partial calendar months (2021-02, 2022-10); NOT the basis for the trend/CV below", "count"),
        "potential_months_in_window": (f"Number of calendar months between this merchant's first and last transaction, clipped to the full-month window {WINDOW_START}..{WINDOW_END}; the denominator for the trend/CV estimate below", "count"),
        "active_months_in_window": ("Of potential_months_in_window, how many actually had a transaction (the rest are zero-filled, not missing)", "count"),
        "normalized_monthly_revenue_trend": ("Linear trend slope of monthly revenue vs. month, divided by mean monthly revenue (regr_slope/regr_avgy), over the zero-filled full-month window; positive = growing. This is a normalised slope, NOT a month-over-month or year-over-year growth rate -- do not read it as a percentage change (增长)", "proportion of mean revenue, per month"),
        "monthly_revenue_cv": ("Coefficient of variation (stddev/mean) of monthly revenue over the same zero-filled window; higher = less stable (稳定性)", "ratio"),
        "low_sample_short_window": (f"True when potential_months_in_window < {MIN_MONTHS_FOR_GROWTH} (including merchants with no activity inside the window at all). Team-chosen threshold; see low_sample_threshold_comparison.csv for how alternates change the count", "boolean"),
        "transactions_in_window": (f"Count of this merchant's transactions falling inside the fixed growth window ({WINDOW_START[0]}-{WINDOW_START[1]:02d}..{WINDOW_END[0]}-{WINDOW_END[1]:02d}) -- NOT the same as total_transactions (all-time). This is the basis for low_sample_few_transactions, since the growth/CV estimate only ever looks at the window: a review found 18 merchants with 10-12 transactions all-time but only 8-9 inside the window, which total_transactions alone would not have caught", "count"),
        "low_sample_few_transactions": (f"True when transactions_in_window < {MIN_TRANSACTIONS_FOR_GROWTH} (NOT total_transactions all-time -- see transactions_in_window). Catches merchants whose first/last transaction span many months but who actually have very few transactions inside the growth window -- a case low_sample_short_window alone misses. Team-chosen threshold", "boolean"),
        "low_sample_few_active_months": (f"True when active_months_in_window < {MIN_ACTIVE_MONTHS_FOR_GROWTH}, i.e. few months with any transaction even though the calendar span looks long. Team-chosen threshold", "boolean"),
        "low_sample_growth_estimate": ("True if ANY of low_sample_short_window / low_sample_few_transactions / low_sample_few_active_months is true. normalized_monthly_revenue_trend and monthly_revenue_cv should be treated with caution (or excluded) for these merchants in any ranking. Replaces a single calendar-span-only check that a review found missed 187 merchants with <10 total transactions and 29 merchants with only 1-2 active months in the window", "boolean"),
        "estimated_bnpl_revenue": ("total_revenue * merchant_take_rate_pct / 100; null when take rate is unavailable", "AUD"),
        "regional_data_coverage_rate_all_sources": ("Share of this merchant's revenue from transactions whose consumer postcode matched all three external sources (census+seifa+ato)", "proportion 0-1"),
        "census_data_coverage_rate": ("Share of this merchant's REVENUE ($) from transactions whose consumer postcode matched the Census source. Amount-weighted -- kept for reference, but the census_* feature averages below are computed as row-count-weighted averages, so this figure can diverge from their true reliability (see census_data_coverage_rate_by_count)", "proportion 0-1"),
        "seifa_data_coverage_rate": ("As above (amount-weighted), for the SEIFA source", "proportion 0-1"),
        "ato_data_coverage_rate": ("As above (amount-weighted), for the ATO source", "proportion 0-1"),
        "regional_data_coverage_rate_all_sources_by_count": ("Share of this merchant's TRANSACTIONS (not revenue) whose consumer postcode matched all three sources. Row-count-weighted -- matches the weighting used to compute the regional feature averages themselves, so this is the PRIMARY figure to use when explaining how reliable those averages are (a review measured up to a 41.35-point gap between the amount- and count-weighted ATO coverage for individual merchants)", "proportion 0-1"),
        "census_data_coverage_rate_by_count": ("Row-count-weighted coverage for the Census source specifically -- primary reliability figure for the census_* feature columns", "proportion 0-1"),
        "seifa_data_coverage_rate_by_count": ("Row-count-weighted coverage for the SEIFA source specifically -- primary reliability figure for the seifa_* feature columns", "proportion 0-1"),
        "ato_data_coverage_rate_by_count": ("Row-count-weighted coverage for the ATO source specifically -- primary reliability figure for the ato_* feature columns", "proportion 0-1"),
        "ato_small_denominator_share_of_matched": ("Of this merchant's ATO-matched transactions, the share whose postcode's ATO ratios rest on fewer than 100 individuals (external_ato's ato_small_denominator quality flag). High values mean the ato_* feature columns for this merchant rest on statistically unstable postcode-level ratios, independent of whether ATO matched at all. Null when the merchant has no ATO-matched transactions", "proportion 0-1"),
    }
    rows = []
    for field, (definition, unit) in core_fields.items():
        rows.append({"field": field, "definition": definition, "unit": unit, "source": "derived"})
    for field in SELECTED_EXTERNAL_FEATURES:
        definition, unit = FEATURE_DEFINITIONS[field]
        rows.append({"field": field, "definition": definition, "unit": unit, "source": FEATURE_SOURCE[field]})
    pd.DataFrame(rows).to_csv(out / "data_dictionary.csv", index=False)

    metadata = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "input_transactions": str(TRANSACTIONS),
        "input_merchants_master": str(MERCHANTS_PARQUET),
        "external_sources": sources_meta,
        "growth_window": {
            "start": f"{WINDOW_START[0]}-{WINDOW_START[1]:02d}", "end": f"{WINDOW_END[0]}-{WINDOW_END[1]:02d}",
            "min_months_for_growth": MIN_MONTHS_FOR_GROWTH,
            "min_transactions_for_growth": MIN_TRANSACTIONS_FOR_GROWTH,
            "min_active_months_for_growth": MIN_ACTIVE_MONTHS_FOR_GROWTH,
        },
        "selected_external_feature_count": len(SELECTED_EXTERNAL_FEATURES),
        "output_rows": n_rows,
        "merchants_with_master_record": n_with_master,
        "merchants_without_master_record": n_without_master,
        "merchants_flagged_low_sample_growth": n_low_sample,
        "merchants_flagged_low_sample_short_window": n_low_sample_short_window,
        "merchants_flagged_low_sample_few_transactions": n_low_sample_few_txn,
        "merchants_flagged_low_sample_few_active_months": n_low_sample_few_active,
        "feature_nonnull_rate_when_matched_min": None if pd.isna(min_nonnull_rate) else float(min_nonnull_rate),
        "distinct_merchant_categories": int(n_categories),
        "total_revenue_reconciliation": {
            "sum_merchant_total_revenue": str(total_revenue_check),
            "sum_transaction_dollar_value": str(total_revenue_txn),
        },
        "versions": {"duckdb": duckdb.__version__, "pandas": pd.__version__},
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    (local / ".gitkeep").touch()
    con.close()
    log("pipeline build complete (still in temp build dir, about to be moved into place)")
    print(json.dumps(metadata, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory (default: merchant_features/results)")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing non-empty output directory instead of refusing to run")
    args = parser.parse_args()
    run(args.output.resolve(), args.overwrite)
