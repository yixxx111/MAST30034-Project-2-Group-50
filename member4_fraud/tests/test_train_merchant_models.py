from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1] / "code"
sys.path.insert(0, str(CODE_DIR))

from train_merchant_models import (  # noqa: E402
    MODEL_FEATURES,
    _build_model,
    _knn_distance_diagnostics,
    _leave_one_out_prediction_values,
    _sort_tuning,
)


def _sample_merchants(rows: int = 24) -> pd.DataFrame:
    rng = np.random.default_rng(50)
    frame = pd.DataFrame(
        rng.lognormal(mean=1.0, sigma=0.6, size=(rows, len(MODEL_FEATURES))),
        columns=MODEL_FEATURES,
    )
    frame.insert(0, "merchant_abn", [str(10_000_000_000 + index) for index in range(rows)])
    return frame


def test_knn_tuning_is_spearman_first() -> None:
    tuning = pd.DataFrame(
        [
            {"model": "KNN", "cv_spearman_mean": 0.60, "cv_mae_mean": 0.08, "cv_rmse_mean": 0.10},
            {"model": "KNN", "cv_spearman_mean": 0.70, "cv_mae_mean": 0.09, "cv_rmse_mean": 0.11},
        ]
    )
    assert _sort_tuning(tuning, "KNN").iloc[0].cv_spearman_mean == 0.70


def test_knn_distance_diagnostics_are_bounded() -> None:
    merchants = _sample_merchants()
    target = np.linspace(0.1, 0.9, len(merchants))
    model = _build_model(
        "KNN",
        {
            "n_neighbors": 3,
            "weights": "distance",
            "p": 1,
            "transformer": "power",
            "n_features": 5,
        },
    )
    model.fit(merchants[MODEL_FEATURES], target)
    diagnostics = _knn_distance_diagnostics(model, merchants, merchants)

    assert len(diagnostics) == len(merchants)
    assert diagnostics.knn_mean_neighbor_distance.ge(0).all()
    assert diagnostics.knn_max_neighbor_distance.ge(
        diagnostics.knn_mean_neighbor_distance
    ).all()
    assert diagnostics.knn_distance_percentile.between(0, 1).all()
    assert diagnostics.knn_confidence_score.between(0, 1).all()
    assert diagnostics.knn_out_of_distribution.dtype == bool


def test_labelled_merchants_use_leave_one_out_predictions() -> None:
    merchants = _sample_merchants()
    target = pd.Series(np.linspace(0.1, 0.9, len(merchants)))
    predictions = _leave_one_out_prediction_values(
        merchants[MODEL_FEATURES],
        target,
        {
            "n_neighbors": 3,
            "weights": "distance",
            "p": 1,
            "transformer": "power",
            "n_features": 5,
        },
    )
    assert len(predictions) == len(merchants)
    assert np.isfinite(predictions).all()
    assert not np.allclose(predictions, target.to_numpy())
