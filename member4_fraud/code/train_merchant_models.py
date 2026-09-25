"""Train a KNN merchant fraud-risk model on 61 labelled merchants.

The target is the mean supplied merchant fraud score per merchant. Model and
K selection use repeated cross-validation on the training merchants; a
stratified merchant holdout is opened once after selection.  Other regressors
are retained as benchmarks only; every downstream merchant score is produced
by the cross-validated KNN pipeline.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from tempfile import gettempdir
from typing import Any

import duckdb
import joblib
import matplotlib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_selection import SelectKBest, mutual_info_regression
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import LeaveOneOut, RepeatedKFold, train_test_split
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PowerTransformer, RobustScaler, StandardScaler
from xgboost import XGBRegressor

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RANDOM_STATE = 50
MODEL_FEATURES = [
    "log_total_revenue",
    "avg_transaction_value",
    "unique_consumers",
    "repeat_consumer_share",
    "active_days",
    "days_since_last_transaction",
    "order_count_90d",
    "log_sales_90d",
    "avg_order_value_90d",
    "unique_consumers_90d",
    "high_value_transaction_ratio_90d",
    "sales_growth_log",
    "transaction_value_cv",
    "merchant_take_rate_pct",
]


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    return value


def _metrics(y_true, prediction) -> dict[str, float]:
    truth = np.asarray(y_true, dtype=float)
    clipped = np.clip(np.asarray(prediction, dtype=float), 0.0, 1.0)
    if np.std(truth) < 1e-12 or np.std(clipped) < 1e-12:
        correlation = 0.0
    else:
        correlation = spearmanr(truth, clipped).statistic
    return {
        "mae": float(mean_absolute_error(truth, clipped)),
        "rmse": float(root_mean_squared_error(truth, clipped)),
        "r2": float(r2_score(truth, clipped)),
        "spearman": float(correlation) if not np.isnan(correlation) else 0.0,
    }


def _pipeline(model: Any, scale: bool) -> Pipeline:
    steps: list[tuple[str, Any]] = [("imputer", SimpleImputer(strategy="median"))]
    if scale:
        steps.append(("scaler", StandardScaler()))
    steps.append(("model", model))
    return Pipeline(steps)


def _mutual_information_scores(x, y):
    """Deterministic nonlinear univariate scores for fold-local selection."""
    return mutual_info_regression(x, y, random_state=RANDOM_STATE)


def _knn_pipeline(parameters: dict[str, Any]) -> Pipeline:
    """Build the complete fold-local KNN preprocessing and model pipeline."""
    settings = dict(parameters)
    transformer_name = settings.pop("transformer")
    selected_features = settings.pop("n_features")
    transformers = {
        "standard": StandardScaler(),
        "robust": RobustScaler(),
        "power": PowerTransformer(method="yeo-johnson", standardize=True),
    }
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("transformer", transformers[transformer_name]),
        ("selector", SelectKBest(_mutual_information_scores, k=selected_features)),
        ("model", KNeighborsRegressor(**settings)),
    ])


def _build_model(name: str, parameters: dict[str, Any]) -> Pipeline:
    if name == "Mean Baseline":
        return _pipeline(DummyRegressor(strategy="mean"), scale=False)
    if name == "KNN":
        return _knn_pipeline(parameters)
    if name == "Linear Regression":
        return _pipeline(LinearRegression(**parameters), scale=True)
    if name == "Random Forest":
        return _pipeline(
            RandomForestRegressor(
                random_state=RANDOM_STATE, n_jobs=-1, **parameters
            ),
            scale=False,
        )
    if name == "XGBoost":
        return _pipeline(
            XGBRegressor(
                objective="reg:squarederror",
                random_state=RANDOM_STATE,
                n_jobs=1,
                verbosity=0,
                **parameters,
            ),
            scale=False,
        )
    raise ValueError(f"Unknown model: {name}")


def _model_grid() -> dict[str, list[dict[str, Any]]]:
    return {
        "Mean Baseline": [{}],
        "KNN": [
            {
                "n_neighbors": k,
                "weights": weights,
                "p": distance_power,
                "transformer": transformer,
                "n_features": n_features,
            }
            for k in (5, 8, 11)
            for weights in ("uniform", "distance")
            for distance_power in (1, 2)
            for transformer in ("standard", "robust", "power")
            for n_features in (5, 8, 11, len(MODEL_FEATURES))
        ],
        "Linear Regression": [{"fit_intercept": True}],
        "Random Forest": [
            {"n_estimators": 400, "max_depth": depth,
             "min_samples_leaf": leaf, "max_features": max_features}
            for depth, leaf, max_features in [
                (2, 4, 0.7), (3, 4, 0.7), (4, 4, 0.7),
                (3, 6, 1.0), (4, 6, 1.0), (None, 8, 0.7),
            ]
        ],
        "XGBoost": [
            {"n_estimators": trees, "max_depth": depth, "learning_rate": rate,
             "min_child_weight": child, "subsample": 0.8,
             "colsample_bytree": 0.8, "reg_lambda": 10.0, "reg_alpha": 0.1}
            for trees, depth, rate, child in [
                (100, 1, 0.03, 5), (250, 1, 0.03, 5),
                (100, 2, 0.03, 5), (250, 2, 0.03, 5),
                (150, 2, 0.05, 10), (300, 2, 0.05, 10),
            ]
        ],
    }


def _save_parquet(frame: pd.DataFrame, path: Path) -> None:
    connection = duckdb.connect()
    connection.register("frame", frame)
    connection.execute(
        f"COPY frame TO '{_sql_path(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.close()


def _diagnostics(data: pd.DataFrame, output: Path) -> None:
    features = data[MODEL_FEATURES]
    diagnostics = pd.DataFrame({
        "feature": MODEL_FEATURES,
        "missing_rate": features.isna().mean().to_numpy(),
        "unique_values": [features[column].nunique(dropna=True) for column in MODEL_FEATURES],
        "standard_deviation": [features[column].std(skipna=True) for column in MODEL_FEATURES],
        "target_pearson": [features[column].corr(data["fraud_probability"]) for column in MODEL_FEATURES],
        "target_spearman": [features[column].corr(data["fraud_probability"], method="spearman") for column in MODEL_FEATURES],
    })
    diagnostics.to_csv(output / "merchant_feature_diagnostics.csv", index=False)

    imputed = pd.DataFrame(
        SimpleImputer(strategy="median").fit_transform(features),
        columns=MODEL_FEATURES,
    )
    correlation = imputed.corr()
    correlation.to_csv(output / "merchant_feature_correlation_matrix.csv")
    pairs = []
    for index, left in enumerate(MODEL_FEATURES):
        for right in MODEL_FEATURES[index + 1:]:
            value = float(correlation.loc[left, right])
            if abs(value) >= 0.90:
                pairs.append({"feature_1": left, "feature_2": right,
                              "correlation": value, "absolute_correlation": abs(value)})
    pd.DataFrame(
        pairs,
        columns=["feature_1", "feature_2", "correlation", "absolute_correlation"],
    ).sort_values("absolute_correlation", ascending=False, ignore_index=True).to_csv(
        output / "merchant_high_correlation_pairs.csv", index=False
    )

    matrix = imputed.to_numpy(dtype=float)
    vif_rows = []
    for index, feature in enumerate(MODEL_FEATURES):
        response = matrix[:, index]
        predictors = np.delete(matrix, index, axis=1)
        if np.std(response) < 1e-12:
            vif = math.inf
        else:
            score = LinearRegression().fit(predictors, response).score(predictors, response)
            vif = math.inf if score >= 1 - 1e-12 else 1 / (1 - score)
        vif_rows.append({"feature": feature, "vif": vif})
    pd.DataFrame(vif_rows).sort_values("vif", ascending=False).to_csv(
        output / "merchant_vif.csv", index=False
    )


def _sort_tuning(frame: pd.DataFrame, model_name: str) -> pd.DataFrame:
    """Use ranking quality for KNN and prediction error for benchmarks."""
    subset = frame.loc[frame.model.eq(model_name)].copy()
    if model_name == "KNN":
        return subset.sort_values(
            ["cv_spearman_mean", "cv_mae_mean", "cv_rmse_mean"],
            ascending=[False, True, True],
        )
    return subset.sort_values(["cv_mae_mean", "cv_rmse_mean"])


def _evaluate_knn_candidates(
    x: pd.DataFrame,
    y: pd.Series,
    splits: list[tuple[np.ndarray, np.ndarray]],
    candidates: list[dict[str, Any]],
) -> pd.DataFrame:
    """Evaluate KNN configurations with every preprocessing step inside folds."""
    rows: list[dict[str, Any]] = []
    for candidate_index, parameters in enumerate(candidates, start=1):
        fold_metrics = []
        for fit_index, validation_index in splits:
            model = _build_model("KNN", parameters)
            model.fit(x.iloc[fit_index], y.iloc[fit_index])
            fold_metrics.append(
                _metrics(y.iloc[validation_index], model.predict(x.iloc[validation_index]))
            )
        metric_frame = pd.DataFrame(fold_metrics)
        rows.append({
            "model": "KNN",
            "candidate_id": f"knn_{candidate_index:03d}",
            "parameters": json.dumps(parameters, sort_keys=True),
            "cv_folds": len(metric_frame),
            "cv_mae_mean": metric_frame.mae.mean(),
            "cv_mae_std": metric_frame.mae.std(),
            "cv_rmse_mean": metric_frame.rmse.mean(),
            "cv_rmse_std": metric_frame.rmse.std(),
            "cv_r2_mean": metric_frame.r2.mean(),
            "cv_spearman_mean": metric_frame.spearman.mean(),
            "cv_spearman_std": metric_frame.spearman.std(),
        })
    return pd.DataFrame(rows)


def _selected_feature_names(model: Pipeline) -> list[str]:
    selector = model.named_steps["selector"]
    return [
        feature for feature, keep in zip(MODEL_FEATURES, selector.get_support()) if keep
    ]


def _nested_knn_validation(
    x: pd.DataFrame,
    y: pd.Series,
    candidates: list[dict[str, Any]],
    output: Path,
) -> pd.DataFrame:
    """Estimate the complete KNN selection procedure without using the holdout."""
    outer_cv = RepeatedKFold(n_splits=5, n_repeats=3, random_state=RANDOM_STATE + 100)
    rows: list[dict[str, Any]] = []
    feature_counts = {feature: 0 for feature in MODEL_FEATURES}
    for outer_fold, (fit_index, validation_index) in enumerate(outer_cv.split(x), start=1):
        inner_cv = RepeatedKFold(
            n_splits=4,
            n_repeats=2,
            random_state=RANDOM_STATE + outer_fold,
        )
        inner_splits = list(inner_cv.split(x.iloc[fit_index]))
        tuning = _evaluate_knn_candidates(
            x.iloc[fit_index].reset_index(drop=True),
            y.iloc[fit_index].reset_index(drop=True),
            inner_splits,
            candidates,
        )
        best = _sort_tuning(tuning, "KNN").iloc[0]
        parameters = json.loads(best["parameters"])
        model = _build_model("KNN", parameters)
        model.fit(x.iloc[fit_index], y.iloc[fit_index])
        prediction = model.predict(x.iloc[validation_index])
        fold_metrics = _metrics(y.iloc[validation_index], prediction)
        selected = _selected_feature_names(model)
        for feature in selected:
            feature_counts[feature] += 1
        rows.append({
            "outer_fold": outer_fold,
            "fit_merchants": len(fit_index),
            "validation_merchants": len(validation_index),
            "selected_parameters": best["parameters"],
            "selected_features": "|".join(selected),
            **fold_metrics,
        })

    nested = pd.DataFrame(rows)
    nested.to_csv(output / "merchant_nested_cv_results.csv", index=False)
    summary = pd.DataFrame([{
        "outer_folds": len(nested),
        "mae_mean": nested.mae.mean(),
        "mae_std": nested.mae.std(),
        "rmse_mean": nested.rmse.mean(),
        "rmse_std": nested.rmse.std(),
        "r2_mean": nested.r2.mean(),
        "r2_std": nested.r2.std(),
        "spearman_mean": nested.spearman.mean(),
        "spearman_std": nested.spearman.std(),
    }])
    summary.to_csv(output / "merchant_nested_cv_summary.csv", index=False)
    pd.DataFrame({
        "feature": MODEL_FEATURES,
        "outer_fold_selections": [feature_counts[feature] for feature in MODEL_FEATURES],
        "outer_folds": len(nested),
        "selection_rate": [feature_counts[feature] / len(nested) for feature in MODEL_FEATURES],
    }).sort_values("selection_rate", ascending=False).to_csv(
        output / "merchant_nested_feature_stability.csv", index=False
    )
    return summary


def _loocv_sensitivity(
    x: pd.DataFrame,
    y: pd.Series,
    merchant_abn: pd.Series,
    parameters: dict[str, Any],
    output: Path,
) -> pd.DataFrame:
    """Run leave-one-merchant-out validation for the already selected pipeline."""
    predictions = _leave_one_out_prediction_values(x, y, parameters)
    metrics = _metrics(y, predictions)
    pd.DataFrame({
        "merchant_abn": merchant_abn.astype(str),
        "actual_fraud_probability": y.to_numpy(),
        "loocv_prediction": predictions,
        "absolute_error": np.abs(y.to_numpy() - predictions),
    }).to_csv(output / "merchant_loocv_predictions.csv", index=False)
    summary = pd.DataFrame([{"folds": len(x), **metrics}])
    summary.to_csv(output / "merchant_loocv_summary.csv", index=False)
    return summary


def _leave_one_out_prediction_values(
    x: pd.DataFrame,
    y: pd.Series,
    parameters: dict[str, Any],
) -> np.ndarray:
    """Predict each labelled merchant without allowing it to neighbour itself."""
    predictions = np.empty(len(x), dtype=float)
    for fit_index, validation_index in LeaveOneOut().split(x):
        model = _build_model("KNN", parameters)
        model.fit(x.iloc[fit_index], y.iloc[fit_index])
        predictions[validation_index[0]] = np.clip(
            model.predict(x.iloc[validation_index])[0], 0.0, 1.0
        )
    return predictions


def _preprocess_knn(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    values: Any = frame
    for step_name in ("imputer", "transformer", "selector"):
        values = model.named_steps[step_name].transform(values)
    return np.asarray(values, dtype=float)


def _knn_distance_diagnostics(
    model: Pipeline,
    training: pd.DataFrame,
    scoring: pd.DataFrame,
) -> pd.DataFrame:
    """Measure how far scored merchants are from labelled merchant support."""
    estimator = model.named_steps["model"]
    neighbour_count = int(estimator.n_neighbors)
    training_matrix = _preprocess_knn(model, training[MODEL_FEATURES])
    scoring_matrix = _preprocess_knn(model, scoring[MODEL_FEATURES])
    query_count = min(neighbour_count + 1, len(training))

    reference_distances, reference_indices = estimator.kneighbors(
        training_matrix, n_neighbors=query_count
    )
    reference_mean = []
    for row_index, (distances, indices) in enumerate(
        zip(reference_distances, reference_indices)
    ):
        kept = [distance for distance, index in zip(distances, indices) if index != row_index]
        reference_mean.append(float(np.mean(kept[:neighbour_count])))
    reference_mean_array = np.asarray(reference_mean)
    sorted_reference = np.sort(reference_mean_array)
    outlier_threshold = float(np.quantile(reference_mean_array, 0.95))

    scoring_distances, scoring_indices = estimator.kneighbors(
        scoring_matrix, n_neighbors=query_count
    )
    training_lookup = {
        str(value): index for index, value in enumerate(training["merchant_abn"])
    }
    mean_distances = []
    max_distances = []
    for merchant_abn, distances, indices in zip(
        scoring["merchant_abn"].astype(str), scoring_distances, scoring_indices
    ):
        own_index = training_lookup.get(merchant_abn)
        kept = [
            float(distance)
            for distance, index in zip(distances, indices)
            if own_index is None or index != own_index
        ][:neighbour_count]
        mean_distances.append(float(np.mean(kept)))
        max_distances.append(float(np.max(kept)))

    mean_distance_array = np.asarray(mean_distances)
    distance_percentile = np.searchsorted(
        sorted_reference, mean_distance_array, side="right"
    ) / len(sorted_reference)
    return pd.DataFrame({
        "knn_mean_neighbor_distance": mean_distance_array,
        "knn_max_neighbor_distance": np.asarray(max_distances),
        "knn_distance_percentile": distance_percentile,
        "knn_confidence_score": 1.0 - distance_percentile,
        "knn_out_of_distribution": mean_distance_array > outlier_threshold,
        "knn_ood_threshold": outlier_threshold,
    })


def run_merchant_model_experiment(
    repo_root: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    module_dir = Path(__file__).resolve().parent
    member4_dir = module_dir.parent if module_dir.name == "code" else module_dir
    repo = Path(repo_root).resolve() if repo_root else member4_dir.parent
    output = Path(
        output_dir or Path(gettempdir()) / "member4_fraud_work" / "merchant_model"
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tuning_dir = output / "tuning"
    models_dir = output / "models"
    figures_dir = output / "figures"
    for directory in (tuning_dir, models_dir, figures_dir):
        directory.mkdir(parents=True, exist_ok=True)

    training_path = output / "merchant_fraud_training_table.parquet"
    scoring_path = output / "merchant_scoring_features_2022-02-28.parquet"
    training = duckdb.sql(f"SELECT * FROM read_parquet('{_sql_path(training_path)}')").df()
    scoring = duckdb.sql(f"SELECT * FROM read_parquet('{_sql_path(scoring_path)}')").df()
    if len(training) != 61 or training["merchant_abn"].nunique() != len(training):
        raise ValueError("Expected one row for each of 61 labelled merchants.")
    if not training["fraud_probability"].between(0, 1).all():
        raise ValueError("Merchant target must be on a 0-1 scale.")

    _diagnostics(training, output)
    target_bins = pd.qcut(
        training["fraud_probability"], q=4, labels=False, duplicates="drop"
    )
    train_index, test_index = train_test_split(
        np.arange(len(training)),
        test_size=0.20,
        random_state=RANDOM_STATE,
        stratify=target_bins,
    )
    train = training.iloc[train_index].reset_index(drop=True)
    test = training.iloc[test_index].reset_index(drop=True)
    split_summary = pd.DataFrame([
        {"split": "train_for_cv", "merchants": len(train),
         "mean_target": train.fraud_probability.mean(),
         "min_target": train.fraud_probability.min(),
         "max_target": train.fraud_probability.max()},
        {"split": "held_out_test", "merchants": len(test),
         "mean_target": test.fraud_probability.mean(),
         "min_target": test.fraud_probability.min(),
         "max_target": test.fraud_probability.max()},
    ])
    split_summary.to_csv(output / "merchant_data_split_summary.csv", index=False)
    test[["merchant_abn"]].to_csv(output / "merchant_held_out_test_ids.csv", index=False)

    x_train, y_train = train[MODEL_FEATURES], train["fraud_probability"]
    x_test, y_test = test[MODEL_FEATURES], test["fraud_probability"]
    cv = RepeatedKFold(n_splits=5, n_repeats=10, random_state=RANDOM_STATE)
    cv_splits = list(cv.split(x_train))
    tuning_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []

    for model_name, candidates in _model_grid().items():
        for candidate_index, parameters in enumerate(candidates, start=1):
            candidate_id = f"{model_name.lower().replace(' ', '_')}_{candidate_index:02d}"
            metrics_by_fold = []
            start = time.perf_counter()
            for fold, (fit_index, validation_index) in enumerate(cv_splits, start=1):
                model = _build_model(model_name, parameters)
                model.fit(x_train.iloc[fit_index], y_train.iloc[fit_index])
                train_metrics = _metrics(
                    y_train.iloc[fit_index], model.predict(x_train.iloc[fit_index])
                )
                validation_metrics = _metrics(
                    y_train.iloc[validation_index], model.predict(x_train.iloc[validation_index])
                )
                metrics_by_fold.append(validation_metrics)
                fold_rows.append({
                    "model": model_name, "candidate_id": candidate_id, "fold": fold,
                    "parameters": json.dumps(parameters, sort_keys=True),
                    **{f"validation_{key}": value for key, value in validation_metrics.items()},
                    "fold_train_mae": train_metrics["mae"],
                    "validation_minus_train_mae": validation_metrics["mae"] - train_metrics["mae"],
                })
            elapsed = time.perf_counter() - start
            metric_frame = pd.DataFrame(metrics_by_fold)
            tuning_rows.append({
                "model": model_name,
                "candidate_id": candidate_id,
                "parameters": json.dumps(parameters, sort_keys=True),
                "cv_folds": len(metric_frame),
                "cv_mae_mean": metric_frame.mae.mean(),
                "cv_mae_std": metric_frame.mae.std(),
                "cv_rmse_mean": metric_frame.rmse.mean(),
                "cv_rmse_std": metric_frame.rmse.std(),
                "cv_r2_mean": metric_frame.r2.mean(),
                "cv_spearman_mean": metric_frame.spearman.mean(),
                "cv_spearman_std": metric_frame.spearman.std(),
                "fit_seconds": elapsed,
            })

    tuning = pd.DataFrame(tuning_rows).sort_values(
        ["model", "cv_mae_mean", "cv_rmse_mean"]
    ).reset_index(drop=True)
    folds = pd.DataFrame(fold_rows)
    tuning.to_csv(output / "merchant_model_tuning_results.csv", index=False)
    folds.to_csv(output / "merchant_cv_fold_results.csv", index=False)
    for model_name in _model_grid():
        safe_name = model_name.lower().replace(" ", "_")
        tuning.loc[tuning.model.eq(model_name)].to_csv(
            tuning_dir / f"{safe_name}_tuning.csv", index=False
        )

    best_rows = pd.concat(
        [_sort_tuning(tuning, model_name).head(1) for model_name in _model_grid()],
        ignore_index=True,
    )
    best_knn_parameters = json.loads(
        best_rows.loc[best_rows.model.eq("KNN"), "parameters"].iloc[0]
    )
    nested_summary = _nested_knn_validation(
        x_train,
        y_train,
        _model_grid()["KNN"],
        output,
    )
    loocv_summary = _loocv_sensitivity(
        x_train,
        y_train,
        train["merchant_abn"],
        best_knn_parameters,
        output,
    )
    model_file_names = {
        "Mean Baseline": "mean_baseline.joblib",
        "KNN": "knn.joblib",
        "Linear Regression": "linear_regression.joblib",
        "Random Forest": "random_forest.joblib",
        "XGBoost": "xgboost.joblib",
    }
    fitted_train_models: dict[str, Pipeline] = {}
    comparison_rows = []
    test_predictions = test[["merchant_abn", "fraud_probability"]].rename(
        columns={"fraud_probability": "actual_fraud_probability"}
    )
    for _, row in best_rows.iterrows():
        model_name = row["model"]
        parameters = json.loads(row["parameters"])
        model = _build_model(model_name, parameters)
        model.fit(x_train, y_train)
        fitted_train_models[model_name] = model
        train_metrics = _metrics(y_train, model.predict(x_train))
        test_prediction = np.clip(model.predict(x_test), 0.0, 1.0)
        test_metrics = _metrics(y_test, test_prediction)
        test_predictions[model_name.lower().replace(" ", "_") + "_prediction"] = test_prediction
        comparison_rows.append({
            "model": model_name,
            "best_parameters": row["parameters"],
            "cv_mae_mean": row["cv_mae_mean"],
            "cv_mae_std": row["cv_mae_std"],
            "cv_rmse_mean": row["cv_rmse_mean"],
            "cv_r2_mean": row["cv_r2_mean"],
            "cv_spearman_mean": row["cv_spearman_mean"],
            "cv_spearman_std": row["cv_spearman_std"],
            "train_mae": train_metrics["mae"],
            "test_mae": test_metrics["mae"],
            "test_rmse": test_metrics["rmse"],
            "test_r2": test_metrics["r2"],
            "test_spearman": test_metrics["spearman"],
            "test_minus_train_mae": test_metrics["mae"] - train_metrics["mae"],
        })

    comparison = pd.DataFrame(comparison_rows).sort_values(
        ["cv_mae_mean", "cv_rmse_mean"], ignore_index=True
    )
    # The merchant-risk methodology is intentionally KNN based.  Repeated CV
    # still selects K, neighbour weighting, and therefore the fitted KNN form;
    # the other model families are diagnostics rather than scoring candidates.
    selected_name = "KNN"
    comparison.insert(1, "selected_by_cv", comparison.model.eq(selected_name))
    comparison.to_csv(output / "merchant_model_comparison.csv", index=False)
    _save_parquet(test_predictions, output / "merchant_test_predictions.parquet")

    forms = pd.DataFrame([
        {"model": "Mean Baseline", "preprocessing": "Training-fold median target",
         "best_form": "Constant mean prediction."},
        {"model": "KNN",
         "preprocessing": "Fold-local median imputation, transformation, mutual-information feature selection",
         "best_form": "Spearman-first tuned nearest-neighbour regression."},
        {"model": "Linear Regression", "preprocessing": "Median imputation + StandardScaler inside each fold",
         "best_form": "Ordinary linear regression on standardized features."},
        {"model": "Random Forest", "preprocessing": "Training-fold median imputation",
         "best_form": "Constrained regression-tree ensemble."},
        {"model": "XGBoost", "preprocessing": "Training-fold median imputation",
         "best_form": "Regularized shallow boosted trees."},
    ]).merge(
        comparison[["model", "best_parameters"]], on="model", how="left"
    )
    forms["saved_model"] = forms.model.map(
        {name: f"models/{filename}" for name, filename in model_file_names.items()}
    )
    forms.to_csv(output / "merchant_best_model_forms.csv", index=False)

    all_x = training[MODEL_FEATURES]
    all_y = training["fraud_probability"]
    refitted_models: dict[str, Pipeline] = {}
    for _, row in comparison.iterrows():
        name = row["model"]
        model = _build_model(name, json.loads(row["best_parameters"]))
        model.fit(all_x, all_y)
        refitted_models[name] = model
        joblib.dump(model, models_dir / model_file_names[name])

    importance_rows = []
    for name in ("Random Forest", "XGBoost"):
        model = refitted_models[name].named_steps["model"]
        for feature, importance in sorted(
            zip(MODEL_FEATURES, model.feature_importances_),
            key=lambda item: item[1], reverse=True,
        ):
            importance_rows.append({"model": name, "feature": feature,
                                    "importance": float(importance)})
    importance = pd.DataFrame(importance_rows)
    importance.to_csv(output / "merchant_tree_feature_importance.csv", index=False)

    linear = refitted_models["Linear Regression"].named_steps["model"]
    linear_table = pd.DataFrame({
        "feature": MODEL_FEATURES,
        "standardized_coefficient": linear.coef_,
        "absolute_coefficient": np.abs(linear.coef_),
        "intercept": linear.intercept_,
    }).sort_values("absolute_coefficient", ascending=False)
    linear_table.to_csv(output / "merchant_linear_regression_coefficients.csv", index=False)

    selected_model = refitted_models[selected_name]
    selector = selected_model.named_steps["selector"]
    selected_feature_table = pd.DataFrame({
        "feature": MODEL_FEATURES,
        "selected": selector.get_support(),
        "mutual_information_score": selector.scores_,
    }).sort_values(["selected", "mutual_information_score"], ascending=[False, False])
    selected_feature_table.to_csv(output / "merchant_selected_features.csv", index=False)
    predictions = scoring[["merchant_abn", "scoring_date", "merchant_name", "merchant_category"]].copy()
    predictions["knn_score"] = np.clip(
        selected_model.predict(scoring[MODEL_FEATURES]), 0.0, 1.0
    )
    labelled_loo_scores = _leave_one_out_prediction_values(
        all_x, all_y, best_knn_parameters
    )
    labelled_score_map = dict(
        zip(training["merchant_abn"].astype(str), labelled_loo_scores)
    )
    scoring_abn = predictions["merchant_abn"].astype(str)
    labelled_mask = scoring_abn.isin(labelled_score_map)
    predictions.loc[labelled_mask, "knn_score"] = scoring_abn.loc[
        labelled_mask
    ].map(labelled_score_map)
    predictions["knn_score_source"] = np.where(
        labelled_mask,
        "leave_one_out_for_labelled_merchant",
        "full_model_for_unlabelled_merchant",
    )
    distance_diagnostics = _knn_distance_diagnostics(selected_model, training, scoring)
    predictions = pd.concat(
        [predictions.reset_index(drop=True), distance_diagnostics.reset_index(drop=True)],
        axis=1,
    )
    # Retain the generic name as a compatibility alias for existing notebooks.
    predictions["predicted_merchant_fraud_probability"] = predictions["knn_score"]
    observed = training[["merchant_abn", "fraud_probability", "target_observations"]].rename(
        columns={"fraud_probability": "observed_merchant_fraud_probability"}
    )
    predictions = predictions.merge(observed, on="merchant_abn", how="left")
    predictions["merchant_fraud_label_available"] = predictions[
        "observed_merchant_fraud_probability"
    ].notna()
    predictions["selected_model"] = selected_name
    _save_parquet(predictions, output / "merchant_fraud_predictions_all.parquet")
    predictions.to_csv(output / "merchant_fraud_predictions_all.csv", index=False)

    plot_table = comparison.sort_values("cv_mae_mean")
    fig, axis = plt.subplots(figsize=(9, 5.5))
    axis.barh(plot_table.model[::-1], plot_table.cv_mae_mean[::-1],
              xerr=plot_table.cv_mae_std[::-1], capsize=4)
    axis.set_xlabel("Repeated-CV MAE (mean ± SD)")
    axis.set_title("Merchant fraud-risk model comparison")
    axis.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures_dir / "merchant_model_comparison.png", dpi=180)
    plt.close(fig)

    top = importance.query("model == @selected_name").head(12)
    if not top.empty:
        fig, axis = plt.subplots(figsize=(9, 6))
        axis.barh(top.feature[::-1], top.importance[::-1])
        axis.set_xlabel("Model importance")
        axis.set_title(f"Merchant model top features: {selected_name}")
        axis.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        fig.savefig(figures_dir / "merchant_selected_model_feature_importance.png", dpi=180)
        plt.close(fig)

    metadata = {
        "task": "retrospective merchant fraud-risk estimation",
        "target": "mean supplied merchant fraud_probability per merchant, 0-1 scale",
        "labelled_merchants": len(training),
        "scored_merchants": len(scoring),
        "training_merchants": len(train),
        "held_out_test_merchants": len(test),
        "validation": {
            "parameter_tuning": "RepeatedKFold: 5 folds x 10 repeats on 48 training merchants",
            "nested_cv": "RepeatedKFold: 5 folds x 3 repeats; inner 4 folds x 2 repeats",
            "loocv_sensitivity": "Leave-one-merchant-out predictions using the selected pipeline",
            "held_out_test": "13 stratified merchants not used in fitting or parameter selection",
        },
        "selection_rule": (
            "KNN fixed as the scoring model; transformation, feature count, neighbour "
            "count, distance and weighting are selected by highest repeated-CV mean "
            "Spearman correlation, with MAE and RMSE as tie-breakers"
        ),
        "selected_model": selected_name,
        "candidate_features": MODEL_FEATURES,
        "selected_features": _selected_feature_names(selected_model),
        "selected_parameters": best_knn_parameters,
        "nested_cv_summary": nested_summary.iloc[0].to_dict(),
        "loocv_summary": loocv_summary.iloc[0].to_dict(),
        "controls": [
            "One target row per merchant; repeated merchant-date labels are aggregated before splitting.",
            "Merchant ABN and direct fraud score are excluded from model features.",
            "Consumer-risk exposure features are excluded from KNN and combined only after merchant scoring.",
            "Imputation, transformation and feature selection are fitted inside each cross-validation fold.",
            "The held-out merchant test set is not used for hyperparameter or model selection.",
            "Labelled merchants receive leave-one-out final scores so they cannot use themselves as nearest neighbours.",
            "Neighbour-distance confidence is calibrated against leave-self-out distances among labelled merchants.",
            "Consumer risk is an upstream model-derived exposure feature and is not a confirmed fraud event.",
        ],
        "limitations": [
            "Only 61 merchants have direct labels, so uncertainty remains high.",
            "This is retrospective risk-score estimation, not causal or real-time fraud detection.",
            "Predictions support ranking review and must not be treated as proof of wrongdoing.",
        ],
    }
    with (output / "merchant_model_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(_json_safe(metadata), handle, indent=2, ensure_ascii=False)

    return comparison


if __name__ == "__main__":
    result = run_merchant_model_experiment()
    print(result.to_string(index=False))
