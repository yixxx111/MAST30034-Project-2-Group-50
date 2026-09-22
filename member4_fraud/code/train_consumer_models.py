"""Train and compare six consumer-day fraud-risk score regressors.

Models: median baseline, mean baseline, KNN, ordinary linear regression,
random forest, and XGBoost. Hyperparameters are selected only on a forward validation period;
the final test period is not used for tuning or model selection. Same-day
transaction aggregates are deliberately included because the supplied label
is defined only at consumer-date grain; the models are retrospective daily
risk estimators, not single-transaction real-time predictors.
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
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RANDOM_STATE = 50
TRAIN_END = pd.Timestamp("2021-11-01")
VALIDATION_END = pd.Timestamp("2022-01-01")
SCORING_DATE = pd.Timestamp("2022-02-28")

# Exact deterministic duplicates of order-count features are deliberately
# excluded: transaction_velocity_7d = order_count_7d / 7 and similarly for 30d.
MODEL_FEATURES = [
    "same_day_order_count",
    "same_day_total_spend",
    "same_day_avg_order_value",
    "same_day_max_order_value",
    "same_day_order_value_std",
    "same_day_unique_merchants",
    "same_day_high_value_transaction_ratio",
    "same_day_order_value_cv",
    "lifetime_order_count",
    "lifetime_spend",
    "order_count_7d",
    "order_count_30d",
    "order_count_90d",
    "spend_7d",
    "spend_30d",
    "spend_90d",
    "avg_order_value_90d",
    "max_order_value_90d",
    "order_value_std_90d",
    "unique_merchants_90d",
    "active_days_90d",
    "weekend_transaction_ratio_90d",
    "high_value_transaction_ratio_90d",
    "days_since_last_transaction",
    "velocity_ratio_7d_to_30d",
    "recent_spend_ratio_7d_to_30d",
    "spend_per_merchant_90d",
    "order_value_cv_90d",
]


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def _metrics(y_true: pd.Series | np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    clipped = np.clip(np.asarray(prediction, dtype=float), 0.0, 1.0)
    truth = np.asarray(y_true, dtype=float)
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


def _build_sklearn_model(model_name: str, parameters: dict[str, Any]) -> Pipeline:
    if model_name == "Median Baseline":
        return _pipeline(DummyRegressor(strategy="median"), scale=False)
    if model_name == "Mean Baseline":
        return _pipeline(DummyRegressor(strategy="mean"), scale=False)
    if model_name == "KNN":
        return _pipeline(
            KNeighborsRegressor(n_jobs=-1, **parameters),
            scale=True,
        )
    if model_name == "Linear Regression":
        return _pipeline(LinearRegression(**parameters), scale=True)
    if model_name == "Random Forest":
        return _pipeline(
            RandomForestRegressor(
                random_state=RANDOM_STATE,
                n_jobs=-1,
                **parameters,
            ),
            scale=False,
        )
    raise ValueError(f"Unsupported sklearn model: {model_name}")


def _xgb_model(parameters: dict[str, Any], early_stopping: bool) -> XGBRegressor:
    settings = {
        "objective": "reg:squarederror",
        "eval_metric": "mae",
        "tree_method": "hist",
        "random_state": RANDOM_STATE,
        "n_jobs": 4,
        **parameters,
    }
    if early_stopping:
        settings["early_stopping_rounds"] = 50
    return XGBRegressor(**settings)


def _candidate_grids() -> dict[str, list[dict[str, Any]]]:
    return {
        "Median Baseline": [{}],
        "Mean Baseline": [{}],
        "KNN": [
            {"n_neighbors": k, "weights": weights, "p": 2}
            for k in (15, 35, 75)
            for weights in ("uniform", "distance")
        ],
        "Linear Regression": [{"fit_intercept": True}],
        "Random Forest": [
            {"n_estimators": 300, "max_depth": 6, "min_samples_leaf": 20, "max_features": 0.7},
            {"n_estimators": 300, "max_depth": 10, "min_samples_leaf": 20, "max_features": 0.7},
            {"n_estimators": 300, "max_depth": 12, "min_samples_leaf": 10, "max_features": "sqrt"},
            {"n_estimators": 400, "max_depth": 8, "min_samples_leaf": 50, "max_features": 1.0},
            {"n_estimators": 400, "max_depth": 12, "min_samples_leaf": 20, "max_features": "sqrt"},
            {"n_estimators": 400, "max_depth": None, "min_samples_leaf": 50, "max_features": 0.7},
        ],
        "XGBoost": [
            {
                "n_estimators": 1000,
                "max_depth": 2,
                "learning_rate": 0.03,
                "min_child_weight": 20,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_lambda": 10.0,
                "reg_alpha": 0.0,
            },
            {
                "n_estimators": 1000,
                "max_depth": 3,
                "learning_rate": 0.03,
                "min_child_weight": 20,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_lambda": 10.0,
                "reg_alpha": 0.1,
            },
            {
                "n_estimators": 800,
                "max_depth": 3,
                "learning_rate": 0.05,
                "min_child_weight": 10,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_lambda": 5.0,
                "reg_alpha": 0.0,
            },
            {
                "n_estimators": 800,
                "max_depth": 4,
                "learning_rate": 0.03,
                "min_child_weight": 30,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_lambda": 10.0,
                "reg_alpha": 0.1,
            },
            {
                "n_estimators": 600,
                "max_depth": 2,
                "learning_rate": 0.08,
                "min_child_weight": 10,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "reg_lambda": 5.0,
                "reg_alpha": 0.0,
            },
            {
                "n_estimators": 800,
                "max_depth": 4,
                "learning_rate": 0.05,
                "min_child_weight": 30,
                "subsample": 0.7,
                "colsample_bytree": 0.8,
                "reg_lambda": 10.0,
                "reg_alpha": 0.1,
            },
        ],
    }


def _training_diagnostics(
    train: pd.DataFrame,
    output_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    features = train[MODEL_FEATURES]
    diagnostics = pd.DataFrame(
        {
            "feature": MODEL_FEATURES,
            "missing_rate": features.isna().mean().values,
            "unique_values": [features[column].nunique(dropna=True) for column in MODEL_FEATURES],
            "standard_deviation": [features[column].std(skipna=True) for column in MODEL_FEATURES],
        }
    )
    diagnostics["near_zero_variance"] = (
        (diagnostics["unique_values"] <= 1)
        | (diagnostics["standard_deviation"].fillna(0).abs() < 1e-12)
    )
    diagnostics.to_csv(output_dir / "consumer_feature_diagnostics.csv", index=False)

    imputer = SimpleImputer(strategy="median")
    imputed = pd.DataFrame(
        imputer.fit_transform(features),
        columns=MODEL_FEATURES,
        index=features.index,
    )
    correlation = imputed.corr(method="pearson")
    correlation.to_csv(output_dir / "consumer_feature_correlation_matrix.csv")

    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(MODEL_FEATURES):
        for right in MODEL_FEATURES[left_index + 1 :]:
            value = float(correlation.loc[left, right])
            if abs(value) >= 0.90:
                pairs.append(
                    {
                        "feature_1": left,
                        "feature_2": right,
                        "correlation": value,
                        "absolute_correlation": abs(value),
                    }
                )
    high_correlation = pd.DataFrame(
        pairs,
        columns=["feature_1", "feature_2", "correlation", "absolute_correlation"],
    ).sort_values("absolute_correlation", ascending=False, ignore_index=True)
    high_correlation.to_csv(output_dir / "consumer_high_correlation_pairs.csv", index=False)

    vif_rows: list[dict[str, Any]] = []
    matrix = imputed.to_numpy(dtype=float)
    for index, feature in enumerate(MODEL_FEATURES):
        response = matrix[:, index]
        predictors = np.delete(matrix, index, axis=1)
        if np.std(response) < 1e-12:
            vif = math.inf
        else:
            score = LinearRegression().fit(predictors, response).score(predictors, response)
            vif = math.inf if score >= 1.0 - 1e-12 else 1.0 / (1.0 - score)
        vif_rows.append({"feature": feature, "vif": vif})
    vif_table = pd.DataFrame(vif_rows).sort_values("vif", ascending=False, ignore_index=True)
    vif_table.to_csv(output_dir / "consumer_vif.csv", index=False)
    return diagnostics, high_correlation, vif_table


def _save_dataframe_as_parquet(dataframe: pd.DataFrame, path: Path) -> None:
    connection = duckdb.connect()
    connection.register("output_frame", dataframe)
    connection.execute(
        f"COPY output_frame TO '{_sql_path(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.close()


def _build_all_consumer_scoring_features(repo: Path, output_path: Path) -> pd.DataFrame:
    transaction_root = (
        repo
        / "member2_curation"
        / "data"
        / "curated"
        / "curated_transactions"
    )
    transaction_glob = _sql_path(
        transaction_root / "order_year=*" / "order_month=*" / "*.parquet"
    )
    scoring_date = SCORING_DATE.strftime("%Y-%m-%d")
    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")
    scoring = connection.execute(
        f"""
        WITH transactions AS (
            SELECT
                cast(order_id AS VARCHAR) AS order_id,
                cast(order_datetime AS DATE) AS order_datetime,
                cast(user_id AS VARCHAR) AS user_id,
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                cast(dollar_value AS DOUBLE) AS dollar_value,
                is_amount_above_p99
            FROM read_parquet('{transaction_glob}', hive_partitioning=true)
            WHERE cast(order_datetime AS DATE) <= DATE '{scoring_date}'
        ), users AS (
            SELECT DISTINCT user_id FROM transactions
        ), history AS (
            SELECT
                u.user_id,
                DATE '{scoring_date}' AS scoring_date,
                count(t.order_id) AS lifetime_order_count,
                coalesce(sum(t.dollar_value), 0.0) AS lifetime_spend,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '7 days'
                ) AS order_count_7d,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '30 days'
                ) AS order_count_30d,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS order_count_90d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '7 days'
                ), 0.0) AS spend_7d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '30 days'
                ), 0.0) AS spend_30d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ), 0.0) AS spend_90d,
                avg(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS avg_order_value_90d,
                max(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS max_order_value_90d,
                stddev_samp(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS order_value_std_90d,
                count(DISTINCT t.merchant_abn) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS unique_merchants_90d,
                count(DISTINCT t.order_datetime) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS active_days_90d,
                avg(CASE WHEN extract(isodow FROM t.order_datetime) IN (6, 7)
                    THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS weekend_transaction_ratio_90d,
                avg(CASE WHEN t.is_amount_above_p99 THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE t.order_datetime >= DATE '{scoring_date}' - INTERVAL '90 days'
                ) AS high_value_transaction_ratio_90d,
                date_diff('day', max(t.order_datetime), DATE '{scoring_date}')
                    AS days_since_last_transaction
            FROM users u
            LEFT JOIN transactions t
                ON t.user_id = u.user_id
               AND t.order_datetime < DATE '{scoring_date}'
            GROUP BY u.user_id
        ), same_day AS (
            SELECT
                u.user_id,
                count(t.order_id) AS same_day_order_count,
                coalesce(sum(t.dollar_value), 0.0) AS same_day_total_spend,
                avg(t.dollar_value) AS same_day_avg_order_value,
                max(t.dollar_value) AS same_day_max_order_value,
                coalesce(stddev_samp(t.dollar_value), 0.0)
                    AS same_day_order_value_std,
                count(DISTINCT t.merchant_abn) AS same_day_unique_merchants,
                avg(CASE WHEN t.is_amount_above_p99 THEN 1.0 ELSE 0.0 END)
                    AS same_day_high_value_transaction_ratio
            FROM users u
            LEFT JOIN transactions t
                ON t.user_id = u.user_id
               AND t.order_datetime = DATE '{scoring_date}'
            GROUP BY u.user_id
        )
        SELECT
            h.*,
            d.same_day_order_count,
            d.same_day_total_spend,
            d.same_day_avg_order_value,
            d.same_day_max_order_value,
            d.same_day_order_value_std,
            d.same_day_unique_merchants,
            d.same_day_high_value_transaction_ratio,
            CASE WHEN d.same_day_avg_order_value > 0
                THEN d.same_day_order_value_std / d.same_day_avg_order_value
            END AS same_day_order_value_cv,
            CASE WHEN h.order_count_30d > 0
                THEN (h.order_count_7d / 7.0) / (h.order_count_30d / 30.0)
            END AS velocity_ratio_7d_to_30d,
            CASE WHEN h.spend_30d > 0 THEN h.spend_7d / h.spend_30d END
                AS recent_spend_ratio_7d_to_30d,
            CASE WHEN h.unique_merchants_90d > 0
                THEN h.spend_90d / h.unique_merchants_90d
            END AS spend_per_merchant_90d,
            CASE WHEN h.avg_order_value_90d > 0
                THEN h.order_value_std_90d / h.avg_order_value_90d
            END AS order_value_cv_90d
        FROM history h
        LEFT JOIN same_day d USING (user_id)
        ORDER BY h.user_id
        """
    ).fetchdf()
    connection.close()
    _save_dataframe_as_parquet(scoring, output_path)
    return scoring


def run_consumer_model_experiment(
    repo_root: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    module_dir = Path(__file__).resolve().parent
    member4_dir = module_dir.parent if module_dir.name == "code" else module_dir
    repo = Path(repo_root).resolve() if repo_root else member4_dir.parent
    results = (
        Path(output_dir).resolve()
        if output_dir
        else (Path(gettempdir()) / "member4_fraud_work").resolve()
    )
    model_output = results / "consumer_model"
    tuning_dir = model_output / "tuning"
    models_dir = model_output / "models"
    figures_dir = model_output / "figures"
    for path in (model_output, tuning_dir, models_dir, figures_dir):
        path.mkdir(parents=True, exist_ok=True)

    feature_path = results / "consumer_fraud_features.parquet"
    if not feature_path.exists():
        raise FileNotFoundError(f"Missing consumer feature table: {feature_path}")
    data = duckdb.sql(f"SELECT * FROM read_parquet('{_sql_path(feature_path)}')").df()
    data["observation_date"] = pd.to_datetime(data["observation_date"])

    if data.duplicated(["user_id", "observation_date"]).any():
        raise RuntimeError("Duplicate consumer entity-date keys found before modelling.")
    if not data["fraud_probability"].between(0, 1).all():
        raise RuntimeError("Fraud target contains values outside [0, 1].")
    forbidden = {
        "user_id",
        "consumer_id",
        "observation_date",
        "fraud_probability",
        "fraud_probability_pct",
        "source_label_rows",
    }
    if forbidden.intersection(MODEL_FEATURES):
        raise RuntimeError("Identifier, date, or target leakage found in MODEL_FEATURES.")

    eligible = data.loc[data["history_days_available"] >= 90].copy()
    train = eligible.loc[eligible["observation_date"] < TRAIN_END].copy()
    validation = eligible.loc[
        (eligible["observation_date"] >= TRAIN_END)
        & (eligible["observation_date"] < VALIDATION_END)
    ].copy()
    test = eligible.loc[eligible["observation_date"] >= VALIDATION_END].copy()
    if train.empty or validation.empty or test.empty:
        raise RuntimeError("A temporal modelling split is empty.")
    if not (
        train["observation_date"].max() < validation["observation_date"].min()
        and validation["observation_date"].max() < test["observation_date"].min()
    ):
        raise RuntimeError("Temporal split overlap detected.")

    split_rows = []
    for name, frame in (("train", train), ("validation", validation), ("test", test)):
        split_rows.append(
            {
                "split": name,
                "rows": len(frame),
                "unique_users": frame["user_id"].nunique(),
                "first_date": frame["observation_date"].min().date(),
                "last_date": frame["observation_date"].max().date(),
                "mean_target": frame["fraud_probability"].mean(),
            }
        )
    split_summary = pd.DataFrame(split_rows)
    split_summary.to_csv(model_output / "consumer_data_split_summary.csv", index=False)
    overlap_summary = pd.DataFrame(
        [
            {
                "comparison": "train_vs_validation",
                "overlapping_users": len(set(train.user_id) & set(validation.user_id)),
            },
            {
                "comparison": "train_vs_test",
                "overlapping_users": len(set(train.user_id) & set(test.user_id)),
            },
            {
                "comparison": "validation_vs_test",
                "overlapping_users": len(set(validation.user_id) & set(test.user_id)),
            },
        ]
    )
    overlap_summary.to_csv(model_output / "consumer_split_user_overlap.csv", index=False)

    diagnostics, high_correlation, vif_table = _training_diagnostics(train, model_output)
    if diagnostics["near_zero_variance"].any():
        problem_features = diagnostics.loc[diagnostics.near_zero_variance, "feature"].tolist()
        raise RuntimeError(f"Near-zero variance model features found: {problem_features}")

    X_train = train[MODEL_FEATURES]
    y_train = train["fraud_probability"]
    X_validation = validation[MODEL_FEATURES]
    y_validation = validation["fraud_probability"]
    X_test = test[MODEL_FEATURES]
    y_test = test["fraud_probability"]

    tuning_rows: list[dict[str, Any]] = []
    best_validation_models: dict[str, Any] = {}
    best_configs: dict[str, dict[str, Any]] = {}
    validation_predictions = validation[["user_id", "observation_date"]].copy()
    validation_predictions["actual_fraud_probability"] = y_validation.to_numpy()

    for model_name, candidates in _candidate_grids().items():
        model_rows: list[dict[str, Any]] = []
        fitted_candidates: list[tuple[Any, np.ndarray, dict[str, Any], dict[str, Any]]] = []
        for candidate_index, parameters in enumerate(candidates, start=1):
            started = time.perf_counter()
            if model_name == "XGBoost":
                estimator = _xgb_model(parameters, early_stopping=True)
                estimator.fit(
                    X_train,
                    y_train,
                    eval_set=[(X_validation, y_validation)],
                    verbose=False,
                )
                best_iteration = getattr(estimator, "best_iteration", None)
            else:
                estimator = _build_sklearn_model(model_name, parameters)
                estimator.fit(X_train, y_train)
                best_iteration = None
            train_prediction = np.clip(estimator.predict(X_train), 0.0, 1.0)
            validation_prediction = np.clip(estimator.predict(X_validation), 0.0, 1.0)
            train_metrics = _metrics(y_train, train_prediction)
            validation_metrics = _metrics(y_validation, validation_prediction)
            row = {
                "model": model_name,
                "config_id": f"{model_name.lower().replace(' ', '_')}_{candidate_index:02d}",
                "parameters": json.dumps(_json_safe(parameters), sort_keys=True),
                "best_iteration": best_iteration,
                "fit_seconds": time.perf_counter() - started,
                **{f"train_{key}": value for key, value in train_metrics.items()},
                **{f"validation_{key}": value for key, value in validation_metrics.items()},
                "validation_minus_train_mae": validation_metrics["mae"] - train_metrics["mae"],
            }
            model_rows.append(row)
            fitted_candidates.append((estimator, validation_prediction, parameters, row))

        model_table = pd.DataFrame(model_rows).sort_values(
            ["validation_mae", "validation_rmse"], ignore_index=True
        )
        model_table["selected_within_model"] = False
        model_table.loc[0, "selected_within_model"] = True
        model_table.to_csv(
            tuning_dir / f"{model_name.lower().replace(' ', '_')}_tuning.csv",
            index=False,
        )
        tuning_rows.extend(model_table.to_dict("records"))
        winning_config_id = model_table.loc[0, "config_id"]
        winner = next(item for item in fitted_candidates if item[3]["config_id"] == winning_config_id)
        best_validation_models[model_name] = winner[0]
        best_configs[model_name] = {
            "parameters": winner[2],
            "validation_row": winner[3],
        }
        validation_predictions[f"prediction_{model_name.lower().replace(' ', '_')}"] = winner[1]

    tuning_results = pd.DataFrame(tuning_rows).sort_values(
        ["model", "validation_mae", "validation_rmse"], ignore_index=True
    )
    tuning_results.to_csv(model_output / "consumer_model_tuning_results.csv", index=False)
    _save_dataframe_as_parquet(
        validation_predictions,
        model_output / "consumer_validation_predictions.parquet",
    )

    # Select the overall model using validation MAE only, before opening the test set.
    validation_best_rows = (
        tuning_results.loc[tuning_results["selected_within_model"]]
        .sort_values(["validation_mae", "validation_rmse"], ignore_index=True)
    )
    selected_model_name = str(validation_best_rows.loc[0, "model"])

    train_validation = pd.concat([train, validation], ignore_index=True)
    X_train_validation = train_validation[MODEL_FEATURES]
    y_train_validation = train_validation["fraud_probability"]
    final_models: dict[str, Any] = {}
    comparison_rows: list[dict[str, Any]] = []
    test_predictions = test[["user_id", "observation_date"]].copy()
    test_predictions["actual_fraud_probability"] = y_test.to_numpy()

    for model_name in _candidate_grids():
        parameters = dict(best_configs[model_name]["parameters"])
        if model_name == "XGBoost":
            best_iteration = best_configs[model_name]["validation_row"]["best_iteration"]
            if best_iteration is not None and not pd.isna(best_iteration):
                parameters["n_estimators"] = int(best_iteration) + 1
            final_model = _xgb_model(parameters, early_stopping=False)
        else:
            final_model = _build_sklearn_model(model_name, parameters)
        final_model.fit(X_train_validation, y_train_validation)
        train_validation_prediction = np.clip(
            final_model.predict(X_train_validation), 0.0, 1.0
        )
        test_prediction = np.clip(final_model.predict(X_test), 0.0, 1.0)
        train_validation_metrics = _metrics(y_train_validation, train_validation_prediction)
        test_metrics = _metrics(y_test, test_prediction)
        validation_row = best_configs[model_name]["validation_row"]
        comparison_rows.append(
            {
                "model": model_name,
                "selected_by_validation": model_name == selected_model_name,
                "best_parameters": json.dumps(_json_safe(parameters), sort_keys=True),
                "validation_mae": validation_row["validation_mae"],
                "validation_rmse": validation_row["validation_rmse"],
                "validation_r2": validation_row["validation_r2"],
                "validation_spearman": validation_row["validation_spearman"],
                "train_validation_mae": train_validation_metrics["mae"],
                "test_mae": test_metrics["mae"],
                "test_rmse": test_metrics["rmse"],
                "test_r2": test_metrics["r2"],
                "test_spearman": test_metrics["spearman"],
                "test_minus_train_validation_mae": (
                    test_metrics["mae"] - train_validation_metrics["mae"]
                ),
            }
        )
        slug = model_name.lower().replace(" ", "_")
        test_predictions[f"prediction_{slug}"] = test_prediction
        joblib.dump(final_model, models_dir / f"{slug}.joblib")
        final_models[model_name] = final_model

    comparison = pd.DataFrame(comparison_rows).sort_values(
        ["validation_mae", "validation_rmse"], ignore_index=True
    )
    comparison.to_csv(model_output / "consumer_model_comparison.csv", index=False)
    _save_dataframe_as_parquet(test_predictions, model_output / "consumer_test_predictions.parquet")

    # Save each selected model's human-readable form and parameters.
    model_forms = pd.DataFrame(
        [
            {
                "model": "Median Baseline",
                "preprocessing": "Median imputation",
                "best_form": "Constant prediction equal to the train+validation target median.",
                "best_parameters": comparison.loc[
                    comparison.model.eq("Median Baseline"), "best_parameters"
                ].iloc[0],
                "saved_model": "models/median_baseline.joblib",
            },
            {
                "model": "Mean Baseline",
                "preprocessing": "Median imputation",
                "best_form": "Constant prediction equal to the train+validation target mean.",
                "best_parameters": comparison.loc[
                    comparison.model.eq("Mean Baseline"), "best_parameters"
                ].iloc[0],
                "saved_model": "models/mean_baseline.joblib",
            },
            {
                "model": "KNN",
                "preprocessing": "Median imputation followed by StandardScaler fitted on training data only.",
                "best_form": "Average or distance-weighted average of the k nearest standardized training rows.",
                "best_parameters": comparison.loc[comparison.model.eq("KNN"), "best_parameters"].iloc[0],
                "saved_model": "models/knn.joblib",
            },
            {
                "model": "Linear Regression",
                "preprocessing": "Median imputation followed by StandardScaler fitted on training data only.",
                "best_form": "prediction = intercept + sum(standardized feature coefficient products).",
                "best_parameters": comparison.loc[
                    comparison.model.eq("Linear Regression"), "best_parameters"
                ].iloc[0],
                "saved_model": "models/linear_regression.joblib",
            },
            {
                "model": "Random Forest",
                "preprocessing": "Median imputation fitted on training data only; no scaling.",
                "best_form": "Average prediction from constrained regression trees.",
                "best_parameters": comparison.loc[
                    comparison.model.eq("Random Forest"), "best_parameters"
                ].iloc[0],
                "saved_model": "models/random_forest.joblib",
            },
            {
                "model": "XGBoost",
                "preprocessing": "Native missing-value handling; no scaling.",
                "best_form": "Regularized additive boosted regression trees with validation early stopping during tuning.",
                "best_parameters": comparison.loc[
                    comparison.model.eq("XGBoost"), "best_parameters"
                ].iloc[0],
                "saved_model": "models/xgboost.joblib",
            },
        ]
    )
    model_forms.to_csv(model_output / "consumer_best_model_forms.csv", index=False)

    linear_pipeline = final_models["Linear Regression"]
    linear_model = linear_pipeline.named_steps["model"]
    linear_coefficients = pd.DataFrame(
        {
            "feature": MODEL_FEATURES,
            "standardized_coefficient": linear_model.coef_,
            "absolute_coefficient": np.abs(linear_model.coef_),
        }
    ).sort_values("absolute_coefficient", ascending=False, ignore_index=True)
    linear_coefficients["intercept"] = linear_model.intercept_
    linear_coefficients.to_csv(
        model_output / "consumer_linear_regression_coefficients.csv", index=False
    )

    importance_tables = []
    for model_name in ("Random Forest", "XGBoost"):
        model = final_models[model_name]
        estimator = model.named_steps["model"] if isinstance(model, Pipeline) else model
        importance = pd.DataFrame(
            {
                "model": model_name,
                "feature": MODEL_FEATURES,
                "importance": estimator.feature_importances_,
            }
        ).sort_values("importance", ascending=False, ignore_index=True)
        importance_tables.append(importance)
    tree_importance = pd.concat(importance_tables, ignore_index=True)
    tree_importance.to_csv(model_output / "consumer_tree_feature_importance.csv", index=False)

    selected_model = final_models[selected_model_name]
    if selected_model_name == "Linear Regression":
        selected_importance = linear_coefficients.rename(
            columns={"absolute_coefficient": "importance"}
        )[["feature", "importance"]]
    elif selected_model_name in ("Random Forest", "XGBoost"):
        selected_importance = tree_importance.loc[
            tree_importance.model.eq(selected_model_name), ["feature", "importance"]
        ].copy()
    else:
        selected_importance = pd.DataFrame(
            {"feature": MODEL_FEATURES, "importance": np.nan}
        )
    selected_importance.insert(0, "selected_model", selected_model_name)
    selected_importance.to_csv(
        model_output / "consumer_selected_model_feature_importance.csv", index=False
    )

    # Model comparison figure.
    plot_table = comparison.sort_values("validation_mae", ascending=True)
    y_positions = np.arange(len(plot_table))
    fig, axis = plt.subplots(figsize=(9, 5))
    axis.barh(y_positions - 0.18, plot_table.validation_mae, height=0.36, label="Validation MAE")
    axis.barh(y_positions + 0.18, plot_table.test_mae, height=0.36, label="Test MAE")
    axis.set_yticks(y_positions, plot_table.model)
    axis.set_xlabel("Mean absolute error")
    axis.set_title("Consumer fraud model comparison")
    axis.legend()
    axis.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures_dir / "consumer_model_comparison.png", dpi=180)
    plt.close(fig)

    # Validation tuning figure with a separate panel per model.
    fig, axes = plt.subplots(3, 2, figsize=(12, 11))
    axes = axes.ravel()
    for axis, model_name in zip(axes, _candidate_grids()):
        subset = tuning_results.loc[tuning_results.model.eq(model_name)].reset_index(drop=True)
        axis.plot(np.arange(1, len(subset) + 1), subset.validation_mae, marker="o")
        axis.set_title(model_name)
        axis.set_xlabel("Candidate ranked within model")
        axis.set_ylabel("Validation MAE")
        axis.grid(alpha=0.25)
    for axis in axes[len(_candidate_grids()):]:
        axis.axis("off")
    fig.suptitle("Validation results for each model's tuning candidates", y=0.995)
    fig.tight_layout()
    fig.savefig(figures_dir / "consumer_model_tuning_validation.png", dpi=180)
    plt.close(fig)

    if selected_importance["importance"].notna().any():
        top = selected_importance.dropna(subset=["importance"]).nlargest(12, "importance")
        fig, axis = plt.subplots(figsize=(9, 6))
        axis.barh(top.feature[::-1], top.importance[::-1])
        axis.set_xlabel("Model importance")
        axis.set_title(f"Top features: {selected_model_name}")
        axis.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        fig.savefig(figures_dir / "consumer_selected_model_feature_importance.png", dpi=180)
        plt.close(fig)

    scoring_features_path = model_output / "consumer_scoring_features_2022-02-28.parquet"
    scoring_features = _build_all_consumer_scoring_features(repo, scoring_features_path)
    scoring_prediction = np.clip(
        selected_model.predict(scoring_features[MODEL_FEATURES]), 0.0, 1.0
    )
    all_predictions = scoring_features[["user_id", "scoring_date"]].copy()
    all_predictions["predicted_consumer_fraud_probability"] = scoring_prediction
    all_predictions["selected_model"] = selected_model_name
    _save_dataframe_as_parquet(
        all_predictions,
        model_output / "consumer_fraud_predictions_all.parquet",
    )
    all_predictions.to_csv(
        model_output / "consumer_fraud_predictions_all.csv", index=False
    )

    metadata = {
        "task": "consumer-day fraud-risk score regression",
        "target": "consumer-date fraud_probability on a 0-1 scale",
        "training_eligibility": "history_days_available >= 90",
        "train_period": f"before {TRAIN_END.date()}",
        "validation_period": f"{TRAIN_END.date()} to {VALIDATION_END.date() - pd.Timedelta(days=1)}",
        "test_period": f"from {VALIDATION_END.date()}",
        "selection_rule": "lowest validation MAE, with validation RMSE as tie-breaker",
        "selected_model": selected_model_name,
        "features": MODEL_FEATURES,
        "scoring_date": SCORING_DATE.date().isoformat(),
        "scored_consumers": len(all_predictions),
        "leakage_controls": [
            "Historical features use transactions strictly before each observation date.",
            "Same-day aggregates are intentionally included because the supplied target exists at consumer-date grain; no individual order is identified or claimed.",
            "Identifier, observation-date, raw target, and duplicate target-scale columns are excluded.",
            "Imputation and scaling are fitted inside training-only pipelines.",
            "Hyperparameters and the final model are selected using validation data only.",
            "The test period is used only after model and hyperparameter selection.",
        ],
        "ethical_limitations": [
            "The supplied target is a probability score, not a confirmed fraud event.",
            "The output is an end-of-day consumer risk-score estimate, not a prospective or real-time single-transaction prediction.",
            "Because 73.08% of labelled consumer-days involve multiple merchants, the score cannot be attributed to a specific order or merchant without an additional mapping key.",
            "Predictions support review prioritisation and must not be treated as proof of wrongdoing.",
            "Gender, state, postcode, names, and other direct demographic identifiers are not model features.",
            "Missing fraud labels are not assigned a zero target.",
            "Temporal drift and repeated users across periods are reported and require monitoring.",
        ],
    }
    with (model_output / "consumer_model_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(_json_safe(metadata), handle, indent=2, ensure_ascii=False)

    return comparison


if __name__ == "__main__":
    final_comparison = run_consumer_model_experiment()
    print(final_comparison.to_string(index=False))
