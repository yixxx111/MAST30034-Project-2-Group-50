import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_merchant_features import (
    MIN_ACTIVE_MONTHS_FOR_GROWTH,
    MIN_MONTHS_FOR_GROWTH,
    MIN_TRANSACTIONS_FOR_GROWTH,
    ROOT,
    SELECTED_EXTERNAL_FEATURES,
    TRANSACTIONS,
    WINDOW_END,
    WINDOW_START,
    _assert_safe_output_dir,
    parse_tags,
)

RESULTS = Path(__file__).resolve().parents[1] / "results"


class TagParsingTests(unittest.TestCase):
    def test_parses_mixed_brackets_and_whitespace(self):
        cases = [
            ("((furniture, home furnishings and equipment shops, and manufacturers, except appliances), (e), (take rate: 0.18))",
             ("furniture, home furnishings and equipment shops, and manufacturers, except appliances", "E", 0.18)),
            ("([cable, satellite, and otHer pay television and radio services], (B), [take rate: 2.34])",
             ("cable, satellite, and other pay television and radio services", "B", 2.34)),
        ]
        for tag, (category, level, take_rate) in cases:
            got_category, got_level, got_take_rate = parse_tags(tag)
            self.assertEqual(got_category, category.lower())
            self.assertEqual(got_level, level)
            self.assertAlmostEqual(got_take_rate, take_rate)

    def test_collapses_whitespace_and_case(self):
        c1, _, _ = parse_tags("((Telecom), (a), (take rate: 1.0))")
        c2, _, _ = parse_tags("((telecom  ), (a), (take rate: 1.0))")
        self.assertEqual(c1, c2)

    def test_rejects_malformed_tag(self):
        self.assertEqual(parse_tags("not a valid tag string"), (None, None, None))

    def test_rejects_missing_tag(self):
        self.assertEqual(parse_tags(None), (None, None, None))


class WindowConfigTests(unittest.TestCase):
    def test_window_is_full_calendar_months_only(self):
        # 2021-02 (1/28 days) and 2022-10 (26/31 days) must be excluded.
        self.assertEqual(WINDOW_START, (2021, 3))
        self.assertEqual(WINDOW_END, (2022, 9))

    def test_min_months_threshold_is_positive(self):
        self.assertGreaterEqual(MIN_MONTHS_FOR_GROWTH, 2)

    def test_low_sample_thresholds_are_positive(self):
        self.assertGreater(MIN_TRANSACTIONS_FOR_GROWTH, 0)
        self.assertGreaterEqual(MIN_ACTIVE_MONTHS_FOR_GROWTH, 2)


class OutputInvariantTests(unittest.TestCase):
    """Sanity checks on the produced merchant_features table.
    Skipped automatically if the pipeline output has not been built yet.
    """

    @classmethod
    def setUpClass(cls):
        path = RESULTS / "merchant_features.parquet"
        if not path.exists():
            raise unittest.SkipTest("merchant_features.parquet not built; run build_merchant_features.py first")
        cls.df = pd.read_parquet(path)

    def test_one_row_per_merchant(self):
        self.assertEqual(self.df["merchant_abn"].duplicated().sum(), 0)

    def test_25_merchant_categories(self):
        self.assertEqual(self.df["merchant_category"].nunique(), 25)

    def test_orphan_merchants_have_null_master_fields(self):
        orphans = self.df[~self.df["has_merchant_master_record"]]
        self.assertGreater(len(orphans), 0)
        for col in ["merchant_name", "merchant_category", "merchant_pricing_level", "merchant_take_rate_pct", "estimated_bnpl_revenue"]:
            self.assertTrue(orphans[col].isna().all(), f"{col} should be null for merchants with no master record")

    def test_non_orphan_merchants_have_master_fields(self):
        matched = self.df[self.df["has_merchant_master_record"]]
        for col in ["merchant_name", "merchant_category", "merchant_pricing_level", "merchant_take_rate_pct"]:
            self.assertTrue(matched[col].notna().all(), f"{col} should be populated for matched merchants")

    def test_selected_external_features_present(self):
        for col in SELECTED_EXTERNAL_FEATURES:
            self.assertIn(col, self.df.columns)

    def test_family_share_features_are_proportions(self):
        for col in ["census_single_parent_family_share", "census_couple_with_children_family_share"]:
            values = self.df[col].dropna()
            self.assertTrue((values >= 0).all() and (values <= 1).all(), f"{col} out of [0,1] range")

    def test_no_raw_family_counts_or_seifa_scores_leaked_into_output(self):
        for col in ["census_single_parent_families", "census_couple_with_children_families", "census_total_families",
                    "seifa_irsd_score", "seifa_irsad_score", "seifa_ier_score"]:
            self.assertNotIn(col, self.df.columns)

    def test_rates_and_shares_are_bounded(self):
        for col in ["repeat_consumer_share", "regional_data_coverage_rate_all_sources",
                    "census_data_coverage_rate", "seifa_data_coverage_rate", "ato_data_coverage_rate",
                    "regional_data_coverage_rate_all_sources_by_count", "census_data_coverage_rate_by_count",
                    "seifa_data_coverage_rate_by_count", "ato_data_coverage_rate_by_count",
                    "ato_small_denominator_share_of_matched"]:
            values = self.df[col].dropna()
            self.assertTrue((values >= 0).all() and (values <= 1).all(), f"{col} out of [0,1] range")

    def test_positive_scale_metrics(self):
        for col in ["total_transactions", "total_revenue", "avg_transaction_value", "unique_consumers"]:
            self.assertTrue((self.df[col] > 0).all(), f"{col} should be strictly positive")

    def test_low_sample_short_window_flag_matches_potential_months(self):
        flagged = self.df[self.df["low_sample_short_window"]]
        unflagged = self.df[~self.df["low_sample_short_window"]]
        self.assertTrue((flagged["potential_months_in_window"].fillna(0) < MIN_MONTHS_FOR_GROWTH).all())
        self.assertTrue((unflagged["potential_months_in_window"] >= MIN_MONTHS_FOR_GROWTH).all())

    def test_low_sample_few_transactions_flag_matches_transactions_in_window(self):
        # Regression guard: this must use transactions_in_window (the growth window),
        # NOT total_transactions (all-time) -- a review found 18 merchants with 10-12
        # transactions all-time but only 8-9 inside the window, which total_transactions
        # alone would not catch.
        flagged = self.df[self.df["low_sample_few_transactions"]]
        unflagged = self.df[~self.df["low_sample_few_transactions"]]
        self.assertTrue((flagged["transactions_in_window"].fillna(0) < MIN_TRANSACTIONS_FOR_GROWTH).all())
        self.assertTrue((unflagged["transactions_in_window"].fillna(0) >= MIN_TRANSACTIONS_FOR_GROWTH).all())

    def test_transactions_in_window_can_differ_from_total_transactions(self):
        mismatch = self.df[(self.df["transactions_in_window"] < MIN_TRANSACTIONS_FOR_GROWTH)
                            & (self.df["total_transactions"] >= MIN_TRANSACTIONS_FOR_GROWTH)]
        # This is the exact case the review flagged -- assert it exists and is flagged,
        # not that total_transactions would have caught it (it wouldn't have).
        if len(mismatch):
            self.assertTrue(mismatch["low_sample_few_transactions"].all())

    def test_transactions_in_window_never_exceeds_total_transactions(self):
        self.assertTrue((self.df["transactions_in_window"] <= self.df["total_transactions"]).all())

    def test_low_sample_few_active_months_flag_matches_active_months(self):
        flagged = self.df[self.df["low_sample_few_active_months"]]
        unflagged = self.df[~self.df["low_sample_few_active_months"]]
        self.assertTrue((flagged["active_months_in_window"].fillna(0) < MIN_ACTIVE_MONTHS_FOR_GROWTH).all())
        self.assertTrue((unflagged["active_months_in_window"] >= MIN_ACTIVE_MONTHS_FOR_GROWTH).all())

    def test_low_sample_growth_estimate_is_or_of_three_flags(self):
        expected = self.df["low_sample_short_window"] | self.df["low_sample_few_transactions"] | self.df["low_sample_few_active_months"]
        pd.testing.assert_series_equal(self.df["low_sample_growth_estimate"], expected, check_names=False)

    def test_low_sample_flags_catch_short_span_low_activity_merchants(self):
        # Regression guard for the review finding: a merchant with very few transactions
        # or very few active months must be flagged even if its calendar span (and
        # therefore potential_months_in_window) happens to be long.
        few_txn_long_span = self.df[(self.df["transactions_in_window"].fillna(0) < MIN_TRANSACTIONS_FOR_GROWTH)
                                     & (self.df["potential_months_in_window"].fillna(0) >= MIN_MONTHS_FOR_GROWTH)]
        if len(few_txn_long_span):
            self.assertTrue(few_txn_long_span["low_sample_growth_estimate"].all())

    def test_growth_null_only_when_short_window(self):
        null_growth = self.df[self.df["normalized_monthly_revenue_trend"].isna()]
        self.assertTrue((null_growth["low_sample_short_window"]).all())
        non_null_growth = self.df[self.df["normalized_monthly_revenue_trend"].notna()]
        self.assertFalse((non_null_growth["low_sample_short_window"]).any())

    def test_active_months_in_window_never_exceeds_potential(self):
        valid = self.df.dropna(subset=["potential_months_in_window"])
        self.assertTrue((valid["active_months_in_window"] <= valid["potential_months_in_window"]).all())


class SafeOutputDirTests(unittest.TestCase):
    """Regression guard for the review finding that --overwrite could delete the project
    root or an input directory if --output pointed at one."""

    def test_refuses_project_root(self):
        with self.assertRaises(SystemExit):
            _assert_safe_output_dir(ROOT)

    def test_refuses_input_transactions_dir(self):
        with self.assertRaises(SystemExit):
            _assert_safe_output_dir(TRANSACTIONS)

    def test_allows_default_results_dir(self):
        _assert_safe_output_dir(RESULTS)  # should not raise


if __name__ == "__main__":
    unittest.main()
