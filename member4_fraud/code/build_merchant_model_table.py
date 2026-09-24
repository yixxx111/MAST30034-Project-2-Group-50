"""Build a leakage-conscious merchant-level fraud-risk modelling table.

The task is retrospective merchant risk estimation at a fixed scoring date.
Direct merchant fraud scores are aggregated to one target per labelled merchant;
they are never used as features. Consumer-day model scores are joined through
transactions and aggregated into merchant exposure features.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

import duckdb


SCORING_DATE = "2022-02-28"


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def build_merchant_model_table(
    repo_root: str | Path | None = None,
    raw_tables_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    curated_transactions_root: str | Path | None = None,
):
    module_dir = Path(__file__).resolve().parent
    member4_dir = module_dir.parent if module_dir.name == "code" else module_dir
    repo = Path(repo_root).resolve() if repo_root else member4_dir.parent
    raw = Path(
        raw_tables_root or repo.parent / "data" / "part1" / "tables"
    ).resolve()
    model_output = Path(
        output_dir or Path(gettempdir()) / "member4_fraud_work" / "merchant_model"
    ).resolve()
    model_output.mkdir(parents=True, exist_ok=True)

    transaction_root = Path(
        curated_transactions_root
        or repo / "member2_curation" / "data" / "curated" / "curated_transactions"
    ).resolve()
    transaction_glob = transaction_root / "order_year=*" / "order_month=*" / "*.parquet"
    consumer_prediction_path = (
        member4_dir / "result" / "consumer_fraud_predictions_all.csv"
    )
    merchant_master_path = (
        repo / "member3_merchant_features" / "results" / "merchant_features.parquet"
    )
    merchant_label_path = raw / "merchant_fraud_probability.csv"

    required = [
        consumer_prediction_path,
        merchant_master_path,
        merchant_label_path,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if not list(transaction_root.rglob("*.parquet")):
        missing.append(str(transaction_root))
    if missing:
        raise FileNotFoundError(f"Required merchant-model inputs missing: {missing}")

    scoring_path = model_output / f"merchant_scoring_features_{SCORING_DATE}.parquet"
    training_path = model_output / "merchant_fraud_training_table.parquet"

    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='8GB'")
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE merchant_scoring_features AS
        WITH transactions AS (
            SELECT
                cast(order_id AS VARCHAR) AS order_id,
                cast(order_datetime AS DATE) AS order_date,
                trim(cast(user_id AS VARCHAR)) AS user_id,
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                cast(dollar_value AS DOUBLE) AS dollar_value,
                cast(is_amount_above_p99 AS BOOLEAN) AS is_amount_above_p99
            FROM read_parquet('{_sql_path(transaction_glob)}', hive_partitioning=true)
            WHERE cast(order_datetime AS DATE) <= DATE '{SCORING_DATE}'
        ), customer_orders AS (
            SELECT merchant_abn, user_id, count(*) AS customer_order_count
            FROM transactions
            GROUP BY 1, 2
        ), repeat_stats AS (
            SELECT
                merchant_abn,
                count(*) AS unique_consumers,
                count(*) FILTER (WHERE customer_order_count >= 2) AS repeat_consumers
            FROM customer_orders
            GROUP BY 1
        ), base AS (
            SELECT
                merchant_abn,
                count(*) AS total_transactions,
                sum(dollar_value) AS total_revenue,
                avg(dollar_value) AS avg_transaction_value,
                max(dollar_value) AS max_transaction_value,
                coalesce(stddev_samp(dollar_value), 0.0) AS transaction_value_std,
                min(order_date) AS first_order_date,
                max(order_date) AS last_order_date,
                count(DISTINCT order_date) AS active_days,
                count(DISTINCT date_trunc('month', order_date)) AS active_months,
                count(*) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '30 days'
                ) AS order_count_30d,
                count(*) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS order_count_90d,
                sum(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '30 days'
                ) AS sales_30d,
                sum(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '60 days'
                      AND order_date <= DATE '{SCORING_DATE}' - INTERVAL '30 days'
                ) AS sales_previous_30d,
                sum(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS sales_90d,
                avg(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS avg_order_value_90d,
                max(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS max_order_value_90d,
                coalesce(stddev_samp(dollar_value) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ), 0.0) AS order_value_std_90d,
                count(DISTINCT user_id) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS unique_consumers_90d,
                avg(CASE WHEN is_amount_above_p99 THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE order_date > DATE '{SCORING_DATE}' - INTERVAL '90 days'
                ) AS high_value_transaction_ratio_90d,
                count(*) FILTER (WHERE order_date = DATE '{SCORING_DATE}')
                    AS same_day_order_count,
                coalesce(sum(dollar_value) FILTER (
                    WHERE order_date = DATE '{SCORING_DATE}'
                ), 0.0) AS same_day_total_sales,
                count(DISTINCT user_id) FILTER (
                    WHERE order_date = DATE '{SCORING_DATE}'
                ) AS same_day_unique_consumers
            FROM transactions
            GROUP BY 1
        ), consumer_predictions AS (
            SELECT
                trim(cast(user_id AS VARCHAR)) AS user_id,
                predicted_consumer_fraud_probability AS consumer_risk
            FROM read_csv_auto('{_sql_path(consumer_prediction_path)}', header=true)
        ), risk_threshold AS (
            SELECT quantile_cont(consumer_risk, 0.9) AS high_risk_cutoff
            FROM consumer_predictions
        ), transaction_risk AS (
            SELECT t.*, p.consumer_risk, q.high_risk_cutoff
            FROM transactions t
            LEFT JOIN consumer_predictions p USING (user_id)
            CROSS JOIN risk_threshold q
        ), risk_by_merchant AS (
            SELECT
                merchant_abn,
                count(*) AS risk_transaction_count,
                count(consumer_risk) AS risk_scored_transaction_count,
                avg(consumer_risk) AS transaction_weighted_consumer_risk,
                sum(dollar_value * consumer_risk)
                    / nullif(sum(dollar_value) FILTER (WHERE consumer_risk IS NOT NULL), 0.0)
                    AS amount_weighted_consumer_risk,
                avg(CASE WHEN consumer_risk >= high_risk_cutoff THEN 1.0 ELSE 0.0 END)
                    FILTER (WHERE consumer_risk IS NOT NULL)
                    AS high_risk_transaction_share,
                sum(dollar_value) FILTER (WHERE consumer_risk >= high_risk_cutoff)
                    / nullif(sum(dollar_value) FILTER (WHERE consumer_risk IS NOT NULL), 0.0)
                    AS high_risk_revenue_share,
                sum(dollar_value * consumer_risk) AS risk_weighted_revenue,
                max(high_risk_cutoff) AS high_consumer_risk_cutoff
            FROM transaction_risk
            GROUP BY 1
        ), unique_consumer_risk AS (
            SELECT merchant_abn, avg(consumer_risk) AS unique_consumer_mean_risk
            FROM (
                SELECT merchant_abn, user_id, max(consumer_risk) AS consumer_risk
                FROM transaction_risk
                WHERE consumer_risk IS NOT NULL
                GROUP BY 1, 2
            )
            GROUP BY 1
        ), static_merchant AS (
            SELECT
                trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
                merchant_name,
                merchant_category,
                merchant_pricing_level,
                cast(merchant_take_rate_pct AS DOUBLE) AS merchant_take_rate_pct,
                cast(has_merchant_master_record AS BOOLEAN) AS has_merchant_master_record
            FROM read_parquet('{_sql_path(merchant_master_path)}')
        )
        SELECT
            s.merchant_abn,
            DATE '{SCORING_DATE}' AS scoring_date,
            s.merchant_name,
            s.merchant_category,
            s.merchant_pricing_level,
            s.merchant_take_rate_pct,
            coalesce(s.has_merchant_master_record, false) AS has_merchant_master_record,
            coalesce(b.total_transactions, 0) AS total_transactions,
            coalesce(b.total_revenue, 0.0) AS total_revenue,
            ln(1.0 + coalesce(b.total_revenue, 0.0)) AS log_total_revenue,
            b.avg_transaction_value,
            b.max_transaction_value,
            b.transaction_value_std,
            coalesce(r.unique_consumers, 0) AS unique_consumers,
            coalesce(r.repeat_consumers, 0) AS repeat_consumers,
            r.repeat_consumers / nullif(r.unique_consumers, 0)::DOUBLE
                AS repeat_consumer_share,
            b.first_order_date,
            b.last_order_date,
            coalesce(b.active_days, 0) AS active_days,
            coalesce(b.active_months, 0) AS active_months,
            date_diff('day', b.last_order_date, DATE '{SCORING_DATE}')
                AS days_since_last_transaction,
            coalesce(b.order_count_30d, 0) AS order_count_30d,
            coalesce(b.order_count_90d, 0) AS order_count_90d,
            coalesce(b.sales_30d, 0.0) AS sales_30d,
            coalesce(b.sales_previous_30d, 0.0) AS sales_previous_30d,
            coalesce(b.sales_90d, 0.0) AS sales_90d,
            ln(1.0 + coalesce(b.sales_90d, 0.0)) AS log_sales_90d,
            b.avg_order_value_90d,
            b.max_order_value_90d,
            b.order_value_std_90d,
            b.unique_consumers_90d,
            b.high_value_transaction_ratio_90d,
            coalesce(b.same_day_order_count, 0) AS same_day_order_count,
            coalesce(b.same_day_total_sales, 0.0) AS same_day_total_sales,
            coalesce(b.same_day_unique_consumers, 0) AS same_day_unique_consumers,
            ln(1.0 + coalesce(b.sales_30d, 0.0))
                - ln(1.0 + coalesce(b.sales_previous_30d, 0.0)) AS sales_growth_log,
            b.total_revenue / nullif(r.unique_consumers, 0) AS revenue_per_consumer,
            b.total_revenue / nullif(b.active_months, 0) AS sales_per_active_month,
            b.transaction_value_std / nullif(b.avg_transaction_value, 0.0)
                AS transaction_value_cv,
            rr.transaction_weighted_consumer_risk,
            rr.amount_weighted_consumer_risk,
            ur.unique_consumer_mean_risk,
            rr.high_risk_transaction_share,
            rr.high_risk_revenue_share,
            rr.risk_weighted_revenue,
            rr.risk_scored_transaction_count
                / nullif(rr.risk_transaction_count, 0)::DOUBLE AS consumer_risk_coverage,
            rr.high_consumer_risk_cutoff
        FROM static_merchant s
        LEFT JOIN base b USING (merchant_abn)
        LEFT JOIN repeat_stats r USING (merchant_abn)
        LEFT JOIN risk_by_merchant rr USING (merchant_abn)
        LEFT JOIN unique_consumer_risk ur USING (merchant_abn)
        ORDER BY s.merchant_abn
        """
    )

    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE merchant_targets AS
        SELECT
            trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
            avg(cast(fraud_probability AS DOUBLE)) / 100.0 AS fraud_probability,
            count(*) AS target_observations,
            coalesce(stddev_samp(cast(fraud_probability AS DOUBLE)) / 100.0, 0.0)
                AS target_std,
            min(cast(order_datetime AS DATE)) AS first_target_date,
            max(cast(order_datetime AS DATE)) AS last_target_date
        FROM read_csv_auto('{_sql_path(merchant_label_path)}')
        GROUP BY 1
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_training_table AS
        SELECT f.*, t.fraud_probability, t.target_observations, t.target_std,
               t.first_target_date, t.last_target_date
        FROM merchant_scoring_features f
        JOIN merchant_targets t USING (merchant_abn)
        ORDER BY f.merchant_abn
        """
    )

    validation = connection.execute(
        """
        SELECT
            (SELECT count(*) - count(DISTINCT merchant_abn)
             FROM merchant_scoring_features) AS duplicate_scoring_merchants,
            (SELECT count(*) FROM merchant_training_table
             WHERE fraud_probability NOT BETWEEN 0 AND 1) AS invalid_targets,
            (SELECT count(*) FROM merchant_training_table) AS labelled_rows,
            (SELECT count(*) FROM merchant_scoring_features) AS scoring_rows
        """
    ).fetchone()
    if validation[0] or validation[1]:
        raise RuntimeError(f"Merchant model-table validation failed: {validation}")

    for path in (scoring_path, training_path):
        if path.exists():
            path.unlink()
    connection.execute(
        f"COPY merchant_scoring_features TO '{_sql_path(scoring_path)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.execute(
        f"COPY merchant_training_table TO '{_sql_path(training_path)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    summary = connection.execute(
        """
        SELECT 'scoring' AS table_name, count(*) AS rows,
               count(DISTINCT merchant_abn) AS merchants,
               NULL::DOUBLE AS mean_target,
               avg(consumer_risk_coverage) AS mean_consumer_risk_coverage
        FROM merchant_scoring_features
        UNION ALL
        SELECT 'training', count(*), count(DISTINCT merchant_abn),
               avg(fraud_probability), avg(consumer_risk_coverage)
        FROM merchant_training_table
        """
    ).fetchdf()
    connection.close()
    return summary


if __name__ == "__main__":
    print(build_merchant_model_table().to_string(index=False))
