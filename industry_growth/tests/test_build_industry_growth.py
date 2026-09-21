import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_industry_growth import CATEGORY_TO_GROUP, GROUP_ORDER, ROOT, TRANSACTIONS, WINDOW_END, WINDOW_START, _assert_safe_output_dir

RESULTS = Path(__file__).resolve().parents[1] / "results"


class MappingTests(unittest.TestCase):
    def test_covers_all_25_categories_exactly_once(self):
        self.assertEqual(len(CATEGORY_TO_GROUP), 25)
        self.assertEqual(len(set(CATEGORY_TO_GROUP.values()) - {"Unclassified"}), 5)

    def test_group_merchant_counts_match_agreed_breakdown(self):
        merchant_features = Path(__file__).resolve().parents[2] / "merchant_features/results/merchant_features.parquet"
        if not merchant_features.exists():
            raise unittest.SkipTest("merchant_features.parquet not built yet")
        df = pd.read_parquet(merchant_features, columns=["merchant_category", "has_merchant_master_record"])
        matched = df[df.has_merchant_master_record].copy()
        matched["industry_group"] = matched["merchant_category"].map(CATEGORY_TO_GROUP)
        counts = matched["industry_group"].value_counts().to_dict()
        expected = {
            "Digital, Technology & Communications": 867,
            "Home, Garden & Living": 827,
            "Mobility, Health & Specialist Services": 806,
            "Creative, Books & Leisure": 827,
            "Art, Gifts, Jewellery & Fashion": 699,
        }
        self.assertEqual(counts, expected)


class WindowConfigTests(unittest.TestCase):
    def test_window_matches_merchant_features(self):
        self.assertEqual(WINDOW_START, (2021, 3))
        self.assertEqual(WINDOW_END, (2022, 9))


class OutputInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = RESULTS / "industry_monthly_revenue.csv"
        if not path.exists():
            raise unittest.SkipTest("industry_monthly_revenue.csv not built; run build_industry_growth.py first")
        cls.monthly = pd.read_csv(path)
        cls.summary = pd.read_csv(RESULTS / "industry_growth_summary.csv")

    def test_partial_months_present_but_flagged(self):
        partial = self.monthly[~self.monthly["is_full_month"]]
        self.assertGreater(len(partial), 0)
        pairs = set(zip(partial.order_year, partial.order_month))
        self.assertEqual(pairs, {(2021, 2), (2022, 10)})

    def test_index_starts_at_100_for_first_full_month(self):
        full = self.monthly[self.monthly["is_full_month"]]
        first = full.sort_values(["industry_group", "order_year", "order_month"]).groupby("industry_group").first()
        self.assertTrue((first["revenue_index"].round(6) == 100).all())

    def test_revenue_shares_sum_to_one(self):
        self.assertAlmostEqual(self.summary["revenue_share"].sum(), 1.0, places=6)

    def test_six_groups_present(self):
        self.assertEqual(set(self.summary["industry_group"]), set(GROUP_ORDER))

    def test_monthly_revenue_positive(self):
        self.assertTrue((self.monthly["revenue"] > 0).all())

    def test_avg_daily_revenue_consistent_with_revenue_and_days(self):
        recomputed = self.monthly["revenue"] / self.monthly["days_with_data"]
        pd.testing.assert_series_equal(recomputed.round(2), self.monthly["avg_daily_revenue"].round(2), check_names=False)

    def test_october_daily_average_not_lower_than_september(self):
        # This is the exact claim the review made: Oct's per-day average revenue should be
        # >= Sep's for every group, even though Oct's month TOTAL is lower.
        sep = self.monthly[(self.monthly.order_year == 2022) & (self.monthly.order_month == 9)].set_index("industry_group")["avg_daily_revenue"]
        oct_ = self.monthly[(self.monthly.order_year == 2022) & (self.monthly.order_month == 10)].set_index("industry_group")["avg_daily_revenue"]
        self.assertTrue((oct_ >= sep).all())


class SafeOutputDirTests(unittest.TestCase):
    """Regression guard for the review finding that --overwrite could delete the entire
    --output directory outright, including the project root or an input directory, if
    --output was pointed at one."""

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
