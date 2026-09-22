"""Train five merchant-level fraud-risk models on 61 labelled merchants.

The target is the mean supplied merchant fraud score per merchant. Model and
hyperparameter selection use repeated cross-validation on the training
merchants; a stratified merchant holdout is opened once after selection.
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import RepeatedKFold, train_test_split
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
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
    "amount_weighted_consumer_risk",
    "unique_consumer_mean_risk",
    "high_risk_revenue_share",
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


def _build_model(name: str, parameters: dict[str, Any]) -> Pipeline:
    if name == "Mean Baseline":
        return _pipeline(DummyRegressor(strategy="mean"), scale=False)
    if name == "KNN":
        return _pipeline(KNeighborsRegressor(**parameters), scale=True)
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
            {"n_neighbors": k, "weights": weights, "p": 2}
            for k in (3, 5, 8, 12)
            for weights in ("uniform", "distance")
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

    best_rows = (
        tuning.sort_values(["cv_mae_mean", "cv_rmse_mean"])
        .groupby("model", as_index=False)
        .first()
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
    selected_name = comparison.iloc[0]["model"]
    comparison.insert(1, "selected_by_cv", comparison.model.eq(selected_name))
    comparison.to_csv(output / "merchant_model_comparison.csv", index=False)
    _save_parquet(test_predictions, output / "merchant_test_predictions.parquet")

    forms = pd.DataFrame([
        {"model": "Mean Baseline", "preprocessing": "Training-fold median target",
         "best_form": "Constant mean prediction."},
        {"model": "KNN", "preprocessing": "Median imputation + StandardScaler inside each fold",
         "best_form": "Nearest-neighbour regression."},
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
    predictions = scoring[["merchant_abn", "scoring_date", "merchant_name", "merchant_category"]].copy()
    predictions["predicted_merchant_fraud_probability"] = np.clip(
        selected_model.predict(scoring[MODEL_FEATURES]), 0.0, 1.0
    )
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
        "validation": "RepeatedKFold: 5 folds x 10 repeats on training merchants",
        "selection_rule": "lowest repeated-CV mean MAE, RMSE tie-breaker",
        "selected_model": selected_name,
        "features": MODEL_FEATURES,
        "controls": [
            "One target row per merchant; repeated merchant-date labels are aggregated before splitting.",
            "Merchant ABN and direct fraud score are excluded from model features.",
            "Imputation and scaling are fitted inside each cross-validation fold.",
            "The held-out merchant test set is not used for hyperparameter or model selection.",
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
