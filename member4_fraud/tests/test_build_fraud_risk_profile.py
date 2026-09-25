from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1] / "code"
sys.path.insert(0, str(CODE_DIR))

from build_fraud_risk_profile import (  # noqa: E402
    _combine_available,
    _weight_sensitivity_summary,
)


def test_weight_scenarios_and_single_source_fallback() -> None:
    consumer = pd.Series([0.8, 0.4, np.nan, np.nan])
    knn = pd.Series([0.2, np.nan, 0.6, np.nan])

    result = _combine_available(consumer, knn, 0.7, 0.3)

    assert np.isclose(result.iloc[0], 0.62)
    assert np.isclose(result.iloc[1], 0.4)
    assert np.isclose(result.iloc[2], 0.6)
    assert np.isnan(result.iloc[3])


def test_weight_sensitivity_summary_uses_neutral_scenario_as_baseline() -> None:
    profile = pd.DataFrame(
        {
            "merchant_abn": ["1", "2", "3"],
            "fraud_risk_70c_30knn": [0.9, 0.4, 0.1],
            "fraud_risk_50c_50knn": [0.8, 0.5, 0.2],
            "fraud_risk_30c_70knn": [0.7, 0.6, 0.3],
        }
    )

    summary = _weight_sensitivity_summary(profile, top_n=2).set_index("scenario")

    assert summary.loc["50c_50knn", "spearman_vs_50c_50knn"] == 1.0
    assert summary.loc["50c_50knn", "mean_absolute_score_change_vs_50c_50knn"] == 0.0
    assert summary.loc["50c_50knn", "top_100_overlap_rate_vs_50c_50knn"] == 1.0
