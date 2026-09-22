"""Build leakage-aware consumer and merchant fraud feature tables with DuckDB.

The modelling row is an entity-observation-date pair. Every transaction feature
uses transactions strictly earlier than that observation date.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

import duckdb
import pandas as pd


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def build_feature_tables(
    repo_root: str | Path | None = None,
    raw_tables_root: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Create the two fraud modelling tables and return a compact audit summary."""

    module_dir = Path(__file__).resolve().parent
    member4_dir = module_dir.parent if module_dir.name == "code" else module_dir
    repo = Path(repo_root).resolve() if repo_root else member4_dir.parent
    raw_root = (
        Path(raw_tables_root).resolve()
        if raw_tables_root
        else (repo.parent / "data" / "part1" / "tables").resolve()
    )
    results = (
        Path(output_dir).resolve()
        if output_dir
        else (Path(gettempdir()) / "member4_fraud_work").resolve()
    )
    results.mkdir(parents=True, exist_ok=True)
    temp_dir = results / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    transaction_root = (
        repo
        / "member2_curation"
        / "data"
        / "curated"
        / "curated_transactions"
    )
    transaction_files = list(transaction_root.rglob("*.parquet"))
    if not transaction_files:
        raise FileNotFoundError(
            "No curated transaction Parquet files found below "
            f"{transaction_root}."
        )

    consumer_fraud_path = raw_root / "consumer_fraud_probability.csv"
    merchant_fraud_path = raw_root / "merchant_fraud_probability.csv"
    for path in (consumer_fraud_path, merchant_fraud_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing fraud label table: {path}")

    consumer_output = results / "consumer_fraud_features.parquet"
    merchant_output = results / "merchant_fraud_features.parquet"
    summary_output = results / "feature_table_summary.csv"
    missingness_output = results / "feature_table_missingness.csv"

    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='8GB'")
    connection.execute(f"PRAGMA temp_directory='{_sql_path(temp_dir)}'")

    transaction_glob = _sql_path(
        transaction_root / "order_year=*" / "order_month=*" / "*.parquet"
    )
    connection.execute(
        f"""
        CREATE OR REPLACE VIEW transactions AS
        SELECT
            cast(order_id AS VARCHAR) AS order_id,
            cast(order_datetime AS DATE) AS order_datetime,
            cast(user_id AS VARCHAR) AS user_id,
            cast(consumer_id AS VARCHAR) AS consumer_id,
            trim(cast(merchant_abn AS VARCHAR)) AS merchant_abn,
            cast(dollar_value AS DOUBLE) AS dollar_value,
            consumer_state,
            consumer_postcode,
            consumer_gender,
            merchant_name,
            merchant_tags,
            merchant_master_matched,
            is_amount_above_p99
        FROM read_parquet('{transaction_glob}', hive_partitioning=true)
        """
    )

    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE consumer_labels AS
        SELECT
            trim(user_id) AS user_id,
            try_cast(order_datetime AS DATE) AS observation_date,
            avg(try_cast(fraud_probability AS DOUBLE)) AS fraud_probability_pct,
            avg(try_cast(fraud_probability AS DOUBLE)) / 100.0 AS fraud_probability,
            count(*) AS source_label_rows
        FROM read_csv_auto(
            '{_sql_path(consumer_fraud_path)}',
            header=true,
            all_varchar=true
        )
        GROUP BY 1, 2
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE merchant_labels AS
        SELECT
            trim(merchant_abn) AS merchant_abn,
            try_cast(order_datetime AS DATE) AS observation_date,
            avg(try_cast(fraud_probability AS DOUBLE)) AS fraud_probability_pct,
            avg(try_cast(fraud_probability AS DOUBLE)) / 100.0 AS fraud_probability,
            count(*) AS source_label_rows
        FROM read_csv_auto(
            '{_sql_path(merchant_fraud_path)}',
            header=true,
            all_varchar=true
        )
        GROUP BY 1, 2
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE consumer_feature_table AS
        WITH history AS (
            SELECT
                l.user_id,
                l.observation_date,
                l.fraud_probability_pct,
                l.fraud_probability,
                l.source_label_rows,
                greatest(
                    0,
                    date_diff(
                        'day',
                        (SELECT min(order_datetime) FROM transactions),
                        l.observation_date
                    )
                ) AS history_days_available,
                count(t.order_id) AS lifetime_order_count,
                coalesce(sum(t.dollar_value), 0.0) AS lifetime_spend,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '7 days'
                ) AS order_count_7d,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '30 days'
                ) AS order_count_30d,
                count(t.order_id) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS order_count_90d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '7 days'
                ), 0.0) AS spend_7d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '30 days'
                ), 0.0) AS spend_30d,
                coalesce(sum(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ), 0.0) AS spend_90d,
                avg(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS avg_order_value_90d,
                max(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS max_order_value_90d,
                stddev_samp(t.dollar_value) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS order_value_std_90d,
                count(DISTINCT t.merchant_abn) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS unique_merchants_90d,
                count(DISTINCT t.order_datetime) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS active_days_90d,
                avg(CASE WHEN extract(isodow FROM t.order_datetime) IN (6, 7)
                    THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS weekend_transaction_ratio_90d,
                avg(CASE WHEN t.is_amount_above_p99 THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE t.order_datetime >= l.observation_date - INTERVAL '90 days'
                ) AS high_value_transaction_ratio_90d,
                date_diff('day', max(t.order_datetime), l.observation_date)
                    AS days_since_last_transaction
            FROM consumer_labels l
            LEFT JOIN transactions t
                ON t.user_id = l.user_id
               AND t.order_datetime < l.observation_date
            GROUP BY 1, 2, 3, 4, 5
        ),
        same_day AS (
            SELECT
                l.user_id,
                l.observation_date,
                count(t.order_id) AS same_day_order_count,
                coalesce(sum(t.dollar_value), 0.0) AS same_day_total_spend,
                avg(t.dollar_value) AS same_day_avg_order_value,
                max(t.dollar_value) AS same_day_max_order_value,
                coalesce(stddev_samp(t.dollar_value), 0.0)
                    AS same_day_order_value_std,
                count(DISTINCT t.merchant_abn) AS same_day_unique_merchants,
                avg(CASE WHEN t.is_amount_above_p99 THEN 1.0 ELSE 0.0 END)
                    AS same_day_high_value_transaction_ratio
            FROM consumer_labels l
            LEFT JOIN transactions t
                ON t.user_id = l.user_id
               AND t.order_datetime = l.observation_date
            GROUP BY 1, 2
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
            least(h.history_days_available, 90) / 90.0 AS history_coverage_90d,
            h.order_count_7d / 7.0 AS transaction_velocity_7d,
            h.order_count_30d / 30.0 AS transaction_velocity_30d,
            CASE WHEN h.order_count_30d > 0
                THEN (h.order_count_7d / 7.0) / (h.order_count_30d / 30.0)
            END AS velocity_ratio_7d_to_30d,
            CASE WHEN h.spend_30d > 0
                THEN h.spend_7d / h.spend_30d
            END AS recent_spend_ratio_7d_to_30d,
            CASE WHEN h.unique_merchants_90d > 0
                THEN h.spend_90d / h.unique_merchants_90d
            END AS spend_per_merchant_90d,
            CASE WHEN h.avg_order_value_90d > 0
                THEN h.order_value_std_90d / h.avg_order_value_90d
            END AS order_value_cv_90d
        FROM history h
        LEFT JOIN same_day d
            ON d.user_id = h.user_id
           AND d.observation_date = h.observation_date
        ORDER BY h.observation_date, h.user_id
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_transaction_history AS
        SELECT
            l.merchant_abn,
            l.observation_date,
            l.fraud_probability_pct,
            l.fraud_probability,
            l.source_label_rows,
            t.order_id,
            t.order_datetime,
            t.user_id,
            t.dollar_value,
            t.merchant_master_matched,
            t.is_amount_above_p99
        FROM merchant_labels l
        LEFT JOIN transactions t
            ON t.merchant_abn = l.merchant_abn
           AND t.order_datetime < l.observation_date
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_customer_counts_90d AS
        SELECT
            merchant_abn,
            observation_date,
            user_id,
            count(*) AS customer_order_count_90d
        FROM merchant_transaction_history
        WHERE order_id IS NOT NULL
          AND order_datetime >= observation_date - INTERVAL '90 days'
        GROUP BY 1, 2, 3
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_base_features AS
        WITH base AS (
            SELECT
                h.merchant_abn,
                h.observation_date,
                h.fraud_probability_pct,
                h.fraud_probability,
                h.source_label_rows,
                greatest(
                    0,
                    date_diff(
                        'day',
                        (SELECT min(order_datetime) FROM transactions),
                        h.observation_date
                    )
                ) AS history_days_available,
                count(h.order_id) AS lifetime_order_count,
                coalesce(sum(h.dollar_value), 0.0) AS lifetime_sales,
                min(h.order_datetime) AS first_transaction_date,
                count(h.order_id) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '7 days'
                ) AS order_count_7d,
                count(h.order_id) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '30 days'
                ) AS order_count_30d,
                count(h.order_id) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '60 days'
                      AND h.order_datetime < h.observation_date - INTERVAL '30 days'
                ) AS order_count_previous_30d,
                count(h.order_id) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS order_count_90d,
                coalesce(sum(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '30 days'
                ), 0.0) AS sales_30d,
                coalesce(sum(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '60 days'
                      AND h.order_datetime < h.observation_date - INTERVAL '30 days'
                ), 0.0) AS sales_previous_30d,
                coalesce(sum(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ), 0.0) AS sales_90d,
                avg(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS avg_order_value_90d,
                max(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS max_order_value_90d,
                stddev_samp(h.dollar_value) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS order_value_std_90d,
                count(DISTINCT h.user_id) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS unique_consumers_90d,
                count(DISTINCT h.order_datetime) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS active_days_90d,
                avg(CASE WHEN h.is_amount_above_p99 THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS high_value_transaction_ratio_90d,
                avg(CASE WHEN h.merchant_master_matched THEN 1.0 ELSE 0.0 END) FILTER (
                    WHERE h.order_datetime >= h.observation_date - INTERVAL '90 days'
                ) AS merchant_master_match_rate_90d,
                date_diff('day', max(h.order_datetime), h.observation_date)
                    AS days_since_last_transaction
            FROM merchant_transaction_history h
            GROUP BY 1, 2, 3, 4, 5
        ), repeat_stats AS (
            SELECT
                merchant_abn,
                observation_date,
                count(*) FILTER (WHERE customer_order_count_90d >= 2)
                    AS repeat_customers_90d
            FROM merchant_customer_counts_90d
            GROUP BY 1, 2
        )
        SELECT
            b.*,
            coalesce(r.repeat_customers_90d, 0) AS repeat_customers_90d
        FROM base b
        LEFT JOIN repeat_stats r USING (merchant_abn, observation_date)
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_transactions_90d AS
        SELECT
            merchant_abn,
            observation_date AS merchant_observation_date,
            order_id,
            order_datetime,
            user_id,
            dollar_value
        FROM merchant_transaction_history
        WHERE order_id IS NOT NULL
          AND order_datetime >= observation_date - INTERVAL '90 days'
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_consumer_risk AS
        WITH scored_transactions AS (
            SELECT
                mt.*,
                cl.fraud_probability AS consumer_fraud_probability
            FROM merchant_transactions_90d mt
            ASOF LEFT JOIN consumer_labels cl
                ON mt.user_id = cl.user_id
               AND mt.order_datetime >= cl.observation_date
        )
        SELECT
            merchant_abn,
            merchant_observation_date AS observation_date,
            count(*) AS risk_window_transaction_count,
            count(consumer_fraud_probability) AS consumer_risk_scored_transactions,
            avg(consumer_fraud_probability) AS avg_consumer_risk,
            sum(dollar_value * consumer_fraud_probability)
                / nullif(sum(dollar_value) FILTER (
                    WHERE consumer_fraud_probability IS NOT NULL
                ), 0.0) AS weighted_consumer_risk,
            sum(dollar_value * consumer_fraud_probability) AS risk_weighted_sales_90d
        FROM scored_transactions
        GROUP BY 1, 2
        """
    )

    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE merchant_feature_table AS
        SELECT
            b.*,
            least(b.history_days_available, 90) / 90.0 AS history_coverage_90d,
            b.order_count_7d / 7.0 AS transaction_velocity_7d,
            b.order_count_30d / 30.0 AS transaction_velocity_30d,
            CASE WHEN b.order_count_30d > 0
                THEN (b.order_count_7d / 7.0) / (b.order_count_30d / 30.0)
            END AS velocity_ratio_7d_to_30d,
            CASE WHEN b.unique_consumers_90d > 0
                THEN b.sales_90d / b.unique_consumers_90d
            END AS revenue_per_customer_90d,
            CASE WHEN b.unique_consumers_90d > 0
                THEN b.repeat_customers_90d / b.unique_consumers_90d::DOUBLE
            END AS repeat_customer_rate_90d,
            ln(1.0 + b.sales_30d) - ln(1.0 + b.sales_previous_30d)
                AS sales_growth_log,
            CASE WHEN b.first_transaction_date IS NOT NULL
                THEN date_diff('month', b.first_transaction_date, b.observation_date) + 1
            END AS active_months,
            CASE WHEN b.first_transaction_date IS NOT NULL
                THEN b.lifetime_sales
                    / (date_diff('month', b.first_transaction_date, b.observation_date) + 1)
            END AS sales_per_active_month,
            CASE WHEN b.avg_order_value_90d > 0
                THEN b.order_value_std_90d / b.avg_order_value_90d
            END AS order_value_cv_90d,
            r.avg_consumer_risk,
            r.weighted_consumer_risk,
            r.risk_weighted_sales_90d,
            coalesce(r.consumer_risk_scored_transactions, 0)
                AS consumer_risk_scored_transactions,
            CASE WHEN coalesce(r.risk_window_transaction_count, 0) > 0
                THEN r.consumer_risk_scored_transactions
                    / r.risk_window_transaction_count::DOUBLE
            END AS consumer_risk_transaction_coverage_90d
        FROM merchant_base_features b
        LEFT JOIN merchant_consumer_risk r USING (merchant_abn, observation_date)
        ORDER BY b.observation_date, b.merchant_abn
        """
    )

    validation = connection.execute(
        """
        SELECT
            (SELECT count(*) - count(DISTINCT (user_id, observation_date))
             FROM consumer_feature_table) AS consumer_duplicate_keys,
            (SELECT count(*) - count(DISTINCT (merchant_abn, observation_date))
             FROM merchant_feature_table) AS merchant_duplicate_keys,
            (SELECT count(*) FROM consumer_feature_table
             WHERE fraud_probability NOT BETWEEN 0 AND 1) AS invalid_consumer_targets,
            (SELECT count(*) FROM merchant_feature_table
             WHERE fraud_probability NOT BETWEEN 0 AND 1) AS invalid_merchant_targets,
            (SELECT count(*) FROM consumer_feature_table
             WHERE order_count_7d > order_count_30d
                OR order_count_30d > order_count_90d
                OR order_count_90d > lifetime_order_count)
                AS invalid_consumer_window_counts,
            (SELECT count(*) FROM merchant_feature_table
             WHERE order_count_7d > order_count_30d
                OR order_count_30d > order_count_90d
                OR order_count_90d > lifetime_order_count)
                AS invalid_merchant_window_counts,
            (SELECT count(*) FROM merchant_feature_table
             WHERE weighted_consumer_risk IS NOT NULL
               AND weighted_consumer_risk NOT BETWEEN 0 AND 1)
                AS invalid_weighted_consumer_risk
        """
    ).fetchone()
    if any(value != 0 for value in validation):
        raise RuntimeError(f"Feature-table validation failed: {validation}")

    for path in (consumer_output, merchant_output):
        if path.exists():
            path.unlink()
    connection.execute(
        f"COPY consumer_feature_table TO '{_sql_path(consumer_output)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.execute(
        f"COPY merchant_feature_table TO '{_sql_path(merchant_output)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    summary = connection.execute(
        """
        SELECT
            'consumer' AS table_name,
            count(*) AS rows,
            count(DISTINCT user_id) AS entities,
            min(observation_date) AS first_observation_date,
            max(observation_date) AS last_observation_date,
            count(*) FILTER (WHERE history_days_available >= 30) AS rows_with_30d_history,
            count(*) FILTER (WHERE history_days_available >= 90) AS rows_with_90d_history,
            count(*) FILTER (WHERE lifetime_order_count = 0) AS rows_without_prior_transactions,
            NULL::DOUBLE AS mean_consumer_risk_transaction_coverage_90d,
            min(fraud_probability) AS min_target,
            avg(fraud_probability) AS mean_target,
            max(fraud_probability) AS max_target
        FROM consumer_feature_table
        UNION ALL
        SELECT
            'merchant',
            count(*),
            count(DISTINCT merchant_abn),
            min(observation_date),
            max(observation_date),
            count(*) FILTER (WHERE history_days_available >= 30),
            count(*) FILTER (WHERE history_days_available >= 90),
            count(*) FILTER (WHERE lifetime_order_count = 0),
            avg(consumer_risk_transaction_coverage_90d),
            min(fraud_probability),
            avg(fraud_probability),
            max(fraud_probability)
        FROM merchant_feature_table
        ORDER BY table_name
        """
    ).fetchdf()
    summary.to_csv(summary_output, index=False)

    missingness = connection.execute(
        """
        SELECT
            'consumer' AS table_name,
            count(*) AS rows,
            count(*) FILTER (WHERE avg_order_value_90d IS NULL) AS missing_avg_order_value_90d,
            count(*) FILTER (WHERE order_value_cv_90d IS NULL) AS missing_order_value_cv_90d,
            NULL::BIGINT AS missing_repeat_customer_rate_90d,
            NULL::BIGINT AS missing_weighted_consumer_risk
        FROM consumer_feature_table
        UNION ALL
        SELECT
            'merchant',
            count(*),
            count(*) FILTER (WHERE avg_order_value_90d IS NULL),
            count(*) FILTER (WHERE order_value_cv_90d IS NULL),
            count(*) FILTER (WHERE repeat_customer_rate_90d IS NULL),
            count(*) FILTER (WHERE weighted_consumer_risk IS NULL)
        FROM merchant_feature_table
        ORDER BY table_name
        """
    ).fetchdf()
    missingness.to_csv(missingness_output, index=False)

    connection.close()
    return summary


if __name__ == "__main__":
    audit = build_feature_tables()
    print(audit.to_string(index=False))
