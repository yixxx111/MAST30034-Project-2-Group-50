"""Build merchant fraud-risk profiles from KNN and consumer exposure scores.

The merchant model is trained separately in ``train_merchant_models.py``. This
module combines two model-derived evidence sources:

1. Amount-weighted exposure to the existing consumer risk scores.
2. Cross-validated KNN merchant-risk predictions trained on the 61 merchants
   with direct merchant-fraud observations.

The original merchant observations remain as reliability fields and training
evidence, but are not inserted directly into the final score.  Both model
signals are converted to merchant percentiles before combining them under three
documented weight scenarios.  The equal-weight scenario remains the default for
backward compatibility.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


SCORING_DATE = "2022-02-28"

FRAUD_WEIGHT_SCENARIOS = {
    "70c_30knn": (0.7, 0.3),
    "50c_50knn": (0.5, 0.5),
    "30c_70knn": (0.3, 0.7),
}


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _percentile_rank(series: pd.Series) -> pd.Series:
    """Average-tie percentile with endpoints 0 and 1."""
    result = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if valid.empty:
        return result
    if len(valid) == 1:
        result.loc[valid.index] = 0.5
        return result
    average_rank = valid.rank(method="average")
    result.loc[valid.index] = (average_rank - 1.0) / (len(valid) - 1.0)
    return result


def _combine_available(
    consumer: pd.Series,
    direct: pd.Series,
    consumer_weight: float,
    direct_weight: float,
) -> pd.Series:
    """Combine available evidence and use the sole source when only one exists."""
    combined = pd.Series(np.nan, index=consumer.index, dtype=float)
    both = consumer.notna() & direct.notna()
    consumer_only = consumer.notna() & direct.isna()
    direct_only = consumer.isna() & direct.notna()
    total_weight = consumer_weight + direct_weight
    combined.loc[both] = (
        consumer_weight * consumer.loc[both]
        + direct_weight * direct.loc[both]
    ) / total_weight
    combined.loc[consumer_only] = consumer.loc[consumer_only]
    combined.loc[direct_only] = direct.loc[direct_only]
    return combined


def _weight_sensitivity_summary(profile: pd.DataFrame, top_n: int = 100) -> pd.DataFrame:
    """Summarise how alternative component weights change merchant rankings."""
    default_column = "fraud_risk_50c_50knn"
    default_scores = profile[default_column]
    default_top = set(profile.nlargest(top_n, default_column)["merchant_abn"])
    rows = []
    for scenario in FRAUD_WEIGHT_SCENARIOS:
        risk_column = f"fraud_risk_{scenario}"
        scores = profile[risk_column]
        valid = default_scores.notna() & scores.notna()
        rank_correlation = (
            default_scores.loc[valid].rank(method="average").corr(
                scores.loc[valid].rank(method="average")
            )
            if valid.sum() > 1
            else np.nan
        )
        scenario_top = set(profile.nlargest(top_n, risk_column)["merchant_abn"])
        rows.append(
            {
                "scenario": scenario,
                "consumer_weight": FRAUD_WEIGHT_SCENARIOS[scenario][0],
                "knn_weight": FRAUD_WEIGHT_SCENARIOS[scenario][1],
                "merchant_count": int(scores.notna().sum()),
                "mean_fraud_risk": scores.mean(),
                "standard_deviation": scores.std(),
                "spearman_vs_50c_50knn": rank_correlation,
                "mean_absolute_score_change_vs_50c_50knn": (
                    scores.loc[valid] - default_scores.loc[valid]
                ).abs().mean(),
                "top_100_overlap_count_vs_50c_50knn": len(
                    scenario_top & default_top
                ),
                "top_100_overlap_rate_vs_50c_50knn": len(
                    scenario_top & default_top
                ) / len(default_top),
            }
        )
    return pd.DataFrame(rows)


def build_fraud_risk_profile(
    repo_root: str | Path | None = None,
    raw_tables_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    curated_transactions_root: str | Path | None = None,
) -> pd.DataFrame:
    module_dir = Path(__file__).resolve().parent
    member4_dir = module_dir.parent if module_dir.name == "code" else module_dir
    repo = Path(repo_root).resolve() if repo_root else member4_dir.parent
    raw = Path(
        raw_tables_root or repo.parent / "data" / "part1" / "tables"
    ).resolve()
    output = Path(output_dir).resolve() if output_dir else member4_dir / "result"
    output.mkdir(parents=True, exist_ok=True)

    transaction_root = Path(
        curated_transactions_root
        or repo / "member2_curation" / "data" / "curated" / "curated_transactions"
    ).resolve()
    transaction_glob = transaction_root / "order_year=*" / "order_month=*" / "*.parquet"
    consumer_risk_path = (
        member4_dir / "result" / "consumer_fraud_predictions_all.csv"
    )
    merchant_knn_path = (
        member4_dir / "result" / "merchant_fraud_predictions_all.csv"
    )
    merchant_feature_path = (
        repo / "member3_merchant_features" / "results" / "merchant_features.parquet"
    )
    consumer_fraud_path = raw / "consumer_fraud_probability.csv"
    merchant_fraud_path = raw / "merchant_fraud_probability.csv"

    required = [
        consumer_risk_path,
        merchant_knn_path,
        merchant_feature_path,
        consumer_fraud_path,
        merchant_fraud_path,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if not list(transaction_root.rglob("*.parquet")):
        missing.append(str(transaction_root))
    if missing:
        raise FileNotFoundError(f"Required fraud-risk inputs are missing: {missing}")

    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='8GB'")
    profile = connection.execute(
        f"""
        WITH transactions AS (
            SELECT
                trim(cast(user_id AS VARCHAR)) AS user_id,
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                cast(order_datetime AS DATE) AS order_date,
                cast(dollar_value AS DOUBLE) AS dollar_value
            FROM read_parquet('{_sql_path(transaction_glob)}', hive_partitioning=true)
            WHERE cast(order_datetime AS DATE) <= DATE '{SCORING_DATE}'
        ), consumer_risk AS (
            SELECT
                trim(cast(user_id AS VARCHAR)) AS user_id,
                cast(predicted_consumer_fraud_probability AS DOUBLE) AS consumer_risk
            FROM read_csv_auto('{_sql_path(consumer_risk_path)}')
        ), observed_consumer_labels AS (
            SELECT DISTINCT trim(cast(user_id AS VARCHAR)) AS user_id
            FROM read_csv_auto('{_sql_path(consumer_fraud_path)}')
        ), transaction_exposure AS (
            SELECT
                t.*,
                r.consumer_risk,
                o.user_id IS NOT NULL AS has_observed_consumer_label
            FROM transactions t
            LEFT JOIN consumer_risk r USING (user_id)
            LEFT JOIN observed_consumer_labels o USING (user_id)
        ), merchant_consumer_exposure AS (
            SELECT
                merchant_abn,
                count(*) AS total_transaction_count,
                count(consumer_risk) AS matched_risk_transaction_count,
                sum(dollar_value) AS total_transaction_amount,
                sum(dollar_value) FILTER (WHERE consumer_risk IS NOT NULL)
                    AS matched_risk_transaction_amount,
                count(*) FILTER (WHERE has_observed_consumer_label)
                    AS observed_label_transaction_count,
                sum(dollar_value) FILTER (WHERE has_observed_consumer_label)
                    AS observed_label_transaction_amount,
                sum(dollar_value * consumer_risk)
                    / nullif(sum(dollar_value) FILTER (WHERE consumer_risk IS NOT NULL), 0.0)
                    AS amount_weighted_consumer_risk,
                avg(consumer_risk) AS transaction_mean_consumer_risk,
                max(consumer_risk) AS maximum_consumer_risk,
                count(DISTINCT user_id) AS total_unique_consumers,
                count(DISTINCT user_id) FILTER (WHERE consumer_risk IS NOT NULL)
                    AS matched_risk_unique_consumers
            FROM transaction_exposure
            GROUP BY 1
        ), merchant_master AS (
            SELECT
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                merchant_name,
                merchant_category
            FROM read_parquet('{_sql_path(merchant_feature_path)}')
        ), merchant_knn AS (
            SELECT
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                cast(knn_score AS DOUBLE) AS knn_score,
                cast(knn_mean_neighbor_distance AS DOUBLE) AS knn_mean_neighbor_distance,
                cast(knn_max_neighbor_distance AS DOUBLE) AS knn_max_neighbor_distance,
                cast(knn_distance_percentile AS DOUBLE) AS knn_distance_percentile,
                cast(knn_confidence_score AS DOUBLE) AS knn_confidence_score,
                cast(knn_out_of_distribution AS BOOLEAN) AS knn_out_of_distribution,
                cast(knn_ood_threshold AS DOUBLE) AS knn_ood_threshold,
                knn_score_source,
                selected_model
            FROM read_csv_auto('{_sql_path(merchant_knn_path)}')
        ), raw_merchant_fraud AS (
            SELECT
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                cast(order_datetime AS DATE) AS fraud_observation_date,
                CASE
                    WHEN abs(cast(fraud_probability AS DOUBLE)) > 1
                        THEN cast(fraud_probability AS DOUBLE) / 100.0
                    ELSE cast(fraud_probability AS DOUBLE)
                END AS observed_fraud_probability
            FROM read_csv_auto('{_sql_path(merchant_fraud_path)}')
        ), merchant_observed_fraud AS (
            SELECT
                merchant_abn,
                count(*) AS merchant_fraud_observation_count,
                avg(observed_fraud_probability) AS mean_observed_merchant_fraud,
                max(observed_fraud_probability) AS max_observed_merchant_fraud,
                arg_max(observed_fraud_probability, fraud_observation_date)
                    AS latest_observed_merchant_fraud,
                min(fraud_observation_date) AS first_merchant_fraud_date,
                max(fraud_observation_date) AS latest_merchant_fraud_date
            FROM raw_merchant_fraud
            GROUP BY 1
        )
        SELECT
            m.merchant_abn,
            m.merchant_name,
            m.merchant_category,
            DATE '{SCORING_DATE}' AS scoring_date,
            e.total_transaction_count,
            e.matched_risk_transaction_count,
            e.matched_risk_transaction_count
                / nullif(e.total_transaction_count, 0)::DOUBLE
                AS consumer_risk_transaction_coverage,
            e.total_transaction_amount,
            e.matched_risk_transaction_amount,
            e.matched_risk_transaction_amount
                / nullif(e.total_transaction_amount, 0.0)
                AS consumer_risk_amount_coverage,
            e.observed_label_transaction_count,
            e.observed_label_transaction_count
                / nullif(e.total_transaction_count, 0)::DOUBLE
                AS observed_consumer_label_transaction_coverage,
            e.observed_label_transaction_amount,
            e.observed_label_transaction_amount
                / nullif(e.total_transaction_amount, 0.0)
                AS observed_consumer_label_amount_coverage,
            e.total_unique_consumers,
            e.matched_risk_unique_consumers,
            e.amount_weighted_consumer_risk,
            e.transaction_mean_consumer_risk,
            e.maximum_consumer_risk,
            k.knn_score,
            k.knn_mean_neighbor_distance,
            k.knn_max_neighbor_distance,
            k.knn_distance_percentile,
            k.knn_confidence_score,
            k.knn_out_of_distribution,
            k.knn_ood_threshold,
            k.knn_score_source,
            k.selected_model AS merchant_risk_model,
            f.merchant_fraud_observation_count,
            f.mean_observed_merchant_fraud,
            f.max_observed_merchant_fraud,
            f.latest_observed_merchant_fraud,
            f.first_merchant_fraud_date,
            f.latest_merchant_fraud_date
        FROM merchant_master m
        LEFT JOIN merchant_consumer_exposure e USING (merchant_abn)
        LEFT JOIN merchant_knn k USING (merchant_abn)
        LEFT JOIN merchant_observed_fraud f USING (merchant_abn)
        ORDER BY m.merchant_abn
        """
    ).fetchdf()
    connection.close()

    if profile["merchant_abn"].duplicated().any():
        raise RuntimeError("Duplicate merchant_abn values found in final fraud-risk profile.")
    probability_columns = [
        "amount_weighted_consumer_risk",
        "transaction_mean_consumer_risk",
        "maximum_consumer_risk",
        "knn_score",
        "knn_distance_percentile",
        "knn_confidence_score",
        "mean_observed_merchant_fraud",
        "max_observed_merchant_fraud",
        "latest_observed_merchant_fraud",
    ]
    for column in probability_columns:
        invalid = profile[column].notna() & ~profile[column].between(0, 1)
        if invalid.any():
            raise RuntimeError(f"{column} contains values outside [0, 1].")

    profile["consumer_exposure_percentile"] = _percentile_rank(
        profile["amount_weighted_consumer_risk"]
    )
    profile["knn_merchant_risk_percentile"] = _percentile_rank(
        profile["knn_score"]
    )
    profile["has_consumer_risk_information"] = profile[
        "amount_weighted_consumer_risk"
    ].notna()
    profile["has_direct_merchant_fraud_information"] = profile[
        "mean_observed_merchant_fraud"
    ].notna()
    profile["has_knn_merchant_risk_information"] = profile["knn_score"].notna()

    has_consumer = profile["has_consumer_risk_information"]
    has_direct = profile["has_direct_merchant_fraud_information"]
    has_knn = profile["has_knn_merchant_risk_information"]
    profile["fraud_evidence_status"] = np.select(
        [has_consumer & has_knn, has_consumer, has_knn],
        ["consumer_and_knn", "consumer_only", "knn_only"],
        default="no_information",
    )

    consumer = profile["consumer_exposure_percentile"]
    knn = profile["knn_merchant_risk_percentile"]
    for scenario, (consumer_weight, knn_weight) in FRAUD_WEIGHT_SCENARIOS.items():
        risk_column = f"fraud_risk_{scenario}"
        safety_column = f"risk_safety_{scenario}"
        profile[risk_column] = _combine_available(
            consumer,
            knn,
            consumer_weight,
            knn_weight,
        )
        profile[safety_column] = 100.0 * (1.0 - profile[risk_column])

    # Retain the original column names so downstream code continues to receive
    # the neutral 50/50 score unless it explicitly selects another scenario.
    profile["fraud_risk_index"] = profile["fraud_risk_50c_50knn"]
    profile["risk_safety_score"] = profile["risk_safety_50c_50knn"]
    profile["fraud_risk_method"] = np.select(
        [has_consumer & has_knn, has_consumer, has_knn],
        ["0.5 consumer exposure percentile + 0.5 KNN merchant risk percentile",
         "consumer exposure only",
         "KNN merchant risk only"],
        default="not available",
    )

    csv_path = output / "merchant_fraud_risk_profile.csv"
    observed_path = output / "observed_merchant_fraud_profile.csv"
    profile.to_csv(csv_path, index=False)
    profile.loc[profile.has_direct_merchant_fraud_information].to_csv(observed_path, index=False)

    summary = pd.DataFrame(
        [
            {"metric": "total_merchants", "value": len(profile)},
            {"metric": "merchants_with_consumer_risk", "value": int(has_consumer.sum())},
            {"metric": "merchants_with_direct_fraud", "value": int(has_direct.sum())},
            {"metric": "merchants_with_knn_risk", "value": int(has_knn.sum())},
            {"metric": "consumer_and_knn", "value": int((has_consumer & has_knn).sum())},
            {"metric": "consumer_only", "value": int((has_consumer & ~has_knn).sum())},
            {"metric": "knn_only", "value": int((~has_consumer & has_knn).sum())},
            {"metric": "no_information", "value": int((~has_consumer & ~has_knn).sum())},
            {"metric": "knn_out_of_distribution", "value": int(profile.knn_out_of_distribution.fillna(False).sum())},
            {"metric": "median_knn_confidence", "value": profile.knn_confidence_score.median()},
            {"metric": "median_consumer_amount_coverage", "value": profile.consumer_risk_amount_coverage.median()},
            {"metric": "mean_consumer_amount_coverage", "value": profile.consumer_risk_amount_coverage.mean()},
            {"metric": "median_observed_consumer_label_amount_coverage", "value": profile.observed_consumer_label_amount_coverage.median()},
            {"metric": "mean_observed_consumer_label_amount_coverage", "value": profile.observed_consumer_label_amount_coverage.mean()},
        ]
    )
    summary.to_csv(output / "fraud_risk_coverage_summary.csv", index=False)

    weight_summary = _weight_sensitivity_summary(profile)
    weight_summary.to_csv(output / "fraud_weight_sensitivity_summary.csv", index=False)

    sensitivity = profile.loc[
        profile.has_knn_merchant_risk_information,
        [
            "merchant_abn",
            "merchant_name",
            "consumer_exposure_percentile",
            "knn_score",
            "knn_merchant_risk_percentile",
            "knn_mean_neighbor_distance",
            "knn_max_neighbor_distance",
            "knn_distance_percentile",
            "knn_confidence_score",
            "knn_out_of_distribution",
            "knn_score_source",
            "fraud_risk_70c_30knn",
            "risk_safety_70c_30knn",
            "fraud_risk_50c_50knn",
            "risk_safety_50c_50knn",
            "fraud_risk_30c_70knn",
            "risk_safety_30c_70knn",
            "fraud_risk_index",
            "risk_safety_score",
        ],
    ].copy()
    sensitivity.to_csv(output / "knn_fraud_risk_scores.csv", index=False)

    return summary


if __name__ == "__main__":
    print(build_fraud_risk_profile().to_string(index=False))
