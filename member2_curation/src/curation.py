"""
The module keeps the supplied source tables immutable.  It read the
three transaction snapshot folders, applies explicit quality rules, joins the
internal consumer and merchant tables, and writes a curated transaction fact
table plus small audit artefacts for the rest of the group.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


class DataQualityError(RuntimeError):
    pass

@dataclass(frozen=True)
class CurationResult:
    curated_transactions_path: Path
    audit_path: Path
    quarantine_path: Path
    merchant_exceptions_path: Path
    join_coverage_path: Path
    snapshot_coverage_path: Path
    input_rows: int
    curated_rows: int
    quarantined_rows: int
    unmatched_merchant_rows: int
    amount_p99: float


def resolve_data_root(data_root: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if data_root is not None:
        candidates.append(Path(data_root))
    configured = os.environ.get("PROJECT2_DATA_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.extend([Path.cwd() / "tables", Path.cwd().parent / "tables"])

    for candidate in candidates:
        if (candidate / "tbl_consumer.csv").is_file() and (
            candidate / "tbl_merchants.parquet"
        ).is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Could not locate Project 2 tables. Pass --data-root or set "
        "PROJECT2_DATA_ROOT to the extracted tables directory."
    )


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _scalar(connection: duckdb.DuckDBPyConnection, query: str) -> Any:
    return connection.execute(query).fetchone()[0]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _reset_output_targets(output_root: Path) -> None:
    """Remove only artefacts owned by this curation stage before a repeatable run."""
    curated_directory = output_root / "curated_transactions"
    if curated_directory.exists():
        shutil.rmtree(curated_directory)
    for filename in (
        "curation_audit.csv",
        "quarantined_transactions.parquet",
        "merchant_match_exceptions.csv",
        "join_coverage.csv",
        "snapshot_coverage.csv",
        "curation_metadata.json",
    ):
        target = output_root / filename
        if target.exists():
            target.unlink()
    output_root.mkdir(parents=True, exist_ok=True)


def _assert_unique_non_null(
    connection: duckdb.DuckDBPyConnection, view_name: str, key_name: str
) -> None:
    """Ensure a dimension cannot multiplies transaction rows during a join."""
    null_count = int(
        _scalar(
            connection,
            f"SELECT count(*) FROM {view_name} WHERE {key_name} IS NULL",
        )
    )
    duplicate_key_count = int(
        _scalar(
            connection,
            f"""
            SELECT count(*)
            FROM (
                SELECT {key_name}
                FROM {view_name}
                GROUP BY {key_name}
                HAVING count(*) > 1
            )
            """,
        )
    )
    if null_count or duplicate_key_count:
        raise DataQualityError(
            f"{view_name}.{key_name} must be non-null and unique before joining; "
            f"found {null_count} null keys and {duplicate_key_count} duplicate keys."
        )


def _copy_query_to_csv(
    connection: duckdb.DuckDBPyConnection, query: str, path: Path
) -> None:
    connection.execute(
        f"COPY ({query}) TO '{_sql_path(path)}' (HEADER, DELIMITER ',')"
    )


def build_curated_transactions(
    data_root: str | Path | None = None,
    output_root: str | Path = "data/curated",
) -> CurationResult:
    """Build a privacy-minimised, internally joined transaction dataset.
    """

    data_root_path = resolve_data_root(data_root)
    output_root_path = Path(output_root).resolve()
    transaction_glob = (
        data_root_path
        / "transactions_*_snapshot"
        / "order_datetime=*"
        / "part-*.parquet"
    )
    if not list(data_root_path.glob("transactions_*_snapshot/order_datetime=*/part-*.parquet")):
        raise FileNotFoundError(
            "No transaction partitions found below "
            f"{data_root_path}/transactions_*_snapshot/order_datetime=*/part-*.parquet"
        )

    _reset_output_targets(output_root_path)
    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")

    try:
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW transactions_normalised AS
            SELECT
                NULLIF(trim(CAST(order_id AS VARCHAR)), '') AS order_id,
                NULLIF(trim(CAST(user_id AS VARCHAR)), '') AS user_id,
                NULLIF(trim(CAST(merchant_abn AS VARCHAR)), '') AS merchant_abn,
                TRY_CAST(dollar_value AS DOUBLE) AS dollar_value,
                TRY_CAST(order_datetime AS DATE) AS order_datetime,
                regexp_extract(filename, '(transactions_[^/]+)', 1) AS source_snapshot
            FROM read_parquet(
                '{_sql_path(transaction_glob)}',
                hive_partitioning = true,
                filename = true,
                union_by_name = true
            )
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW user_mapping AS
            SELECT
                NULLIF(trim(CAST(user_id AS VARCHAR)), '') AS user_id,
                NULLIF(trim(CAST(consumer_id AS VARCHAR)), '') AS consumer_id
            FROM read_parquet('{_sql_path(data_root_path / 'consumer_user_details.parquet')}')
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW consumers AS
            SELECT
                NULLIF(trim(CAST(consumer_id AS VARCHAR)), '') AS consumer_id,
                upper(NULLIF(trim(CAST(state AS VARCHAR)), '')) AS consumer_state,
                CASE
                    WHEN regexp_full_match(trim(CAST(postcode AS VARCHAR)), '^[0-9]{{1,4}}$')
                        THEN lpad(trim(CAST(postcode AS VARCHAR)), 4, '0')
                    ELSE NULL
                END AS consumer_postcode,
                regexp_full_match(trim(CAST(postcode AS VARCHAR)), '^[0-9]{{1,4}}$')
                    AS consumer_postcode_valid,
                upper(NULLIF(trim(CAST(gender AS VARCHAR)), '')) AS consumer_gender
            FROM read_csv_auto('{_sql_path(data_root_path / 'tbl_consumer.csv')}', header = true)
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW merchants AS
            SELECT
                NULLIF(trim(CAST(merchant_abn AS VARCHAR)), '') AS merchant_abn,
                NULLIF(trim(CAST(name AS VARCHAR)), '') AS merchant_name,
                NULLIF(trim(CAST(tags AS VARCHAR)), '') AS merchant_tags
            FROM read_parquet('{_sql_path(data_root_path / 'tbl_merchants.parquet')}')
            """
        )

        _assert_unique_non_null(connection, "user_mapping", "user_id")
        _assert_unique_non_null(connection, "consumers", "consumer_id")
        _assert_unique_non_null(connection, "merchants", "merchant_abn")

        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW validation_classified AS
            SELECT
                *,
                CASE
                    WHEN order_id IS NULL THEN 'missing_order_id'
                    WHEN user_id IS NULL THEN 'missing_user_id'
                    WHEN merchant_abn IS NULL THEN 'missing_merchant_abn'
                    WHEN order_datetime IS NULL THEN 'invalid_order_datetime'
                    WHEN dollar_value IS NULL OR NOT isfinite(dollar_value)
                        THEN 'invalid_dollar_value'
                    WHEN dollar_value <= 0 THEN 'non_positive_dollar_value'
                    ELSE NULL
                END AS rejection_reason
            FROM transactions_normalised
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW valid_base AS
            SELECT order_id, user_id, merchant_abn, dollar_value, order_datetime, source_snapshot
            FROM validation_classified
            WHERE rejection_reason IS NULL
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW duplicate_order_counts AS
            SELECT
                order_id,
                count(*) AS rows_for_order
            FROM valid_base
            GROUP BY order_id
            HAVING count(*) > 1
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW duplicate_order_status AS
            SELECT
                t.order_id,
                c.rows_for_order,
                count(DISTINCT struct_pack(
                    user_id := t.user_id,
                    merchant_abn := t.merchant_abn,
                    dollar_value := t.dollar_value,
                    order_datetime := t.order_datetime
                )) AS distinct_payloads
            FROM valid_base t
            INNER JOIN duplicate_order_counts c USING (order_id)
            GROUP BY t.order_id, c.rows_for_order
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW duplicate_classified AS
            SELECT
                t.*,
                s.rows_for_order,
                s.distinct_payloads,
                row_number() OVER (
                    PARTITION BY t.order_id
                    ORDER BY t.source_snapshot, t.order_datetime, t.user_id, t.merchant_abn
                ) AS duplicate_rank
            FROM valid_base t
            INNER JOIN duplicate_order_status s USING (order_id)
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW accepted_transactions AS
            SELECT b.order_id, b.user_id, b.merchant_abn, b.dollar_value, b.order_datetime, b.source_snapshot
            FROM valid_base b
            LEFT JOIN duplicate_order_counts d USING (order_id)
            WHERE d.order_id IS NULL
            UNION ALL
            SELECT order_id, user_id, merchant_abn, dollar_value, order_datetime, source_snapshot
            FROM duplicate_classified
            WHERE distinct_payloads = 1 AND duplicate_rank = 1
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW quarantined_transactions AS
            SELECT
                order_id, user_id, merchant_abn, dollar_value, order_datetime,
                source_snapshot, rejection_reason
            FROM validation_classified
            WHERE rejection_reason IS NOT NULL
            UNION ALL
            SELECT
                order_id, user_id, merchant_abn, dollar_value, order_datetime,
                source_snapshot,
                CASE
                    WHEN distinct_payloads > 1 THEN 'conflicting_duplicate_order_id'
                    ELSE 'exact_duplicate_order_id'
                END AS rejection_reason
            FROM duplicate_classified
            WHERE distinct_payloads > 1
               OR (rows_for_order > 1 AND duplicate_rank > 1)
            """
        )

        input_rows = int(_scalar(connection, "SELECT count(*) FROM transactions_normalised"))
        base_invalid_rows = int(
            _scalar(connection, "SELECT count(*) FROM validation_classified WHERE rejection_reason IS NOT NULL")
        )
        accepted_rows = int(_scalar(connection, "SELECT count(*) FROM accepted_transactions"))
        quarantined_rows = int(_scalar(connection, "SELECT count(*) FROM quarantined_transactions"))
        if accepted_rows == 0:
            raise DataQualityError("No valid transactions remain after applying curation rules.")

        amount_p99 = float(
            _scalar(
                connection,
                "SELECT quantile_cont(dollar_value, 0.99) FROM accepted_transactions",
            )
        )
        if not math.isfinite(amount_p99):
            raise DataQualityError("Could not calculate a finite p99 transaction amount.")

        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW transactions_with_consumer_id AS
            SELECT t.*, u.consumer_id
            FROM accepted_transactions t
            INNER JOIN user_mapping u USING (user_id)
            """
        )
        after_user_join = int(
            _scalar(connection, "SELECT count(*) FROM transactions_with_consumer_id")
        )
        if after_user_join != accepted_rows:
            raise DataQualityError(
                "User mapping join changed the accepted transaction row count: "
                f"{accepted_rows} -> {after_user_join}."
            )

        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW transactions_with_consumer AS
            SELECT
                t.*,
                c.consumer_state,
                c.consumer_postcode,
                c.consumer_postcode_valid,
                c.consumer_gender
            FROM transactions_with_consumer_id t
            INNER JOIN consumers c USING (consumer_id)
            """
        )
        after_consumer_join = int(
            _scalar(connection, "SELECT count(*) FROM transactions_with_consumer")
        )
        if after_consumer_join != accepted_rows:
            raise DataQualityError(
                "Consumer join changed the accepted transaction row count: "
                f"{accepted_rows} -> {after_consumer_join}."
            )

        connection.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW curated_transactions AS
            SELECT
                t.order_id,
                t.order_datetime,
                year(t.order_datetime) AS order_year,
                month(t.order_datetime) AS order_month,
                t.user_id,
                t.consumer_id,
                t.merchant_abn,
                t.dollar_value,
                t.consumer_state,
                t.consumer_postcode,
                t.consumer_postcode_valid,
                t.consumer_gender,
                m.merchant_name,
                m.merchant_tags,
                t.source_snapshot,
                m.merchant_abn IS NOT NULL AS merchant_master_matched,
                t.dollar_value > {amount_p99!r} AS is_amount_above_p99
            FROM transactions_with_consumer t
            LEFT JOIN merchants m USING (merchant_abn)
            """
        )
        curated_rows = int(_scalar(connection, "SELECT count(*) FROM curated_transactions"))
        if curated_rows != accepted_rows:
            raise DataQualityError(
                "Merchant join changed the accepted transaction row count: "
                f"{accepted_rows} -> {curated_rows}."
            )
        duplicate_curated_orders = int(
            _scalar(
                connection,
                """
                SELECT count(*)
                FROM (
                    SELECT order_id
                    FROM curated_transactions
                    GROUP BY order_id
                    HAVING count(*) > 1
                )
                """,
            )
        )
        if duplicate_curated_orders:
            raise DataQualityError(
                f"Curated output contains {duplicate_curated_orders} duplicate order IDs."
            )

        unmatched_merchant_rows = int(
            _scalar(
                connection,
                "SELECT count(*) FROM curated_transactions WHERE NOT merchant_master_matched",
            )
        )
        exact_duplicate_rows = int(
            _scalar(
                connection,
                "SELECT count(*) FROM quarantined_transactions WHERE rejection_reason = 'exact_duplicate_order_id'",
            )
        )
        conflicting_duplicate_rows = int(
            _scalar(
                connection,
                "SELECT count(*) FROM quarantined_transactions WHERE rejection_reason = 'conflicting_duplicate_order_id'",
            )
        )

        curated_path = output_root_path / "curated_transactions"
        connection.execute(
            f"""
            COPY (
                SELECT *
                FROM curated_transactions
                ORDER BY order_datetime, order_id
            ) TO '{_sql_path(curated_path)}'
            (FORMAT PARQUET, PARTITION_BY (order_year, order_month))
            """
        )
        quarantine_path = output_root_path / "quarantined_transactions.parquet"
        connection.execute(
            f"""
            COPY (
                SELECT * FROM quarantined_transactions
                ORDER BY order_datetime NULLS LAST, order_id NULLS LAST
            ) TO '{_sql_path(quarantine_path)}' (FORMAT PARQUET)
            """
        )

        merchant_exceptions_path = output_root_path / "merchant_match_exceptions.csv"
        _copy_query_to_csv(
            connection,
            """
            SELECT
                merchant_abn,
                count(*) AS transaction_count,
                sum(dollar_value) AS total_dollar_value,
                min(order_datetime) AS first_order_datetime,
                max(order_datetime) AS last_order_datetime
            FROM curated_transactions
            WHERE NOT merchant_master_matched
            GROUP BY merchant_abn
            ORDER BY transaction_count DESC, merchant_abn
            """,
            merchant_exceptions_path,
        )
        snapshot_coverage_path = output_root_path / "snapshot_coverage.csv"
        _copy_query_to_csv(
            connection,
            """
            SELECT
                source_snapshot,
                count(*) AS transaction_count,
                min(order_datetime) AS first_order_datetime,
                max(order_datetime) AS last_order_datetime
            FROM transactions_normalised
            GROUP BY source_snapshot
            ORDER BY first_order_datetime
            """,
            snapshot_coverage_path,
        )

        audit_path = output_root_path / "curation_audit.csv"
        _write_csv(
            audit_path,
            [
                {
                    "stage": "raw_transaction_partitions",
                    "input_rows": input_rows,
                    "output_rows": input_rows,
                    "quarantined_rows": 0,
                    "removed_pct_of_input": 0.0,
                    "notes": "All discovered transaction snapshot partitions; coverage is recorded separately.",
                },
                {
                    "stage": "basic_validation",
                    "input_rows": input_rows,
                    "output_rows": input_rows - base_invalid_rows,
                    "quarantined_rows": base_invalid_rows,
                    "removed_pct_of_input": base_invalid_rows / input_rows,
                    "notes": "Missing keys, invalid dates, and non-positive or non-finite amounts are quarantined.",
                },
                {
                    "stage": "duplicate_order_resolution",
                    "input_rows": input_rows - base_invalid_rows,
                    "output_rows": accepted_rows,
                    "quarantined_rows": exact_duplicate_rows + conflicting_duplicate_rows,
                    "removed_pct_of_input": (exact_duplicate_rows + conflicting_duplicate_rows) / input_rows,
                    "notes": "One exact duplicate is retained; all conflicting duplicate order IDs are quarantined.",
                },
                {
                    "stage": "consumer_internal_joins",
                    "input_rows": accepted_rows,
                    "output_rows": after_consumer_join,
                    "quarantined_rows": 0,
                    "removed_pct_of_input": 0.0,
                    "notes": "Inner joins are required to preserve row count or the run fails.",
                },
                {
                    "stage": "merchant_left_join_and_output",
                    "input_rows": after_consumer_join,
                    "output_rows": curated_rows,
                    "quarantined_rows": 0,
                    "removed_pct_of_input": 0.0,
                    "notes": "Unmatched merchants are retained with merchant_master_matched = false.",
                },
            ],
        )
        join_coverage_path = output_root_path / "join_coverage.csv"
        _write_csv(
            join_coverage_path,
            [
                {
                    "join_name": "transactions_to_user_mapping",
                    "input_rows": accepted_rows,
                    "matched_rows": after_user_join,
                    "unmatched_rows": accepted_rows - after_user_join,
                    "match_rate": after_user_join / accepted_rows,
                },
                {
                    "join_name": "user_mapping_to_consumers",
                    "input_rows": after_user_join,
                    "matched_rows": after_consumer_join,
                    "unmatched_rows": after_user_join - after_consumer_join,
                    "match_rate": after_consumer_join / after_user_join,
                },
                {
                    "join_name": "transactions_to_merchants",
                    "input_rows": curated_rows,
                    "matched_rows": curated_rows - unmatched_merchant_rows,
                    "unmatched_rows": unmatched_merchant_rows,
                    "match_rate": (curated_rows - unmatched_merchant_rows) / curated_rows,
                },
            ],
        )
        metadata_path = output_root_path / "curation_metadata.json"
        metadata = {
            "pipeline_stage": "transaction_curation",
            "run_utc": datetime.now(timezone.utc).isoformat(),
            "data_root": str(data_root_path),
            "transaction_glob": str(transaction_glob),
            "amount_p99": amount_p99,
            "result": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(
                    CurationResult(
                        curated_path,
                        audit_path,
                        quarantine_path,
                        merchant_exceptions_path,
                        join_coverage_path,
                        snapshot_coverage_path,
                        input_rows,
                        curated_rows,
                        quarantined_rows,
                        unmatched_merchant_rows,
                        amount_p99,
                    )
                ).items()
            },
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return CurationResult(
            curated_transactions_path=curated_path,
            audit_path=audit_path,
            quarantine_path=quarantine_path,
            merchant_exceptions_path=merchant_exceptions_path,
            join_coverage_path=join_coverage_path,
            snapshot_coverage_path=snapshot_coverage_path,
            input_rows=input_rows,
            curated_rows=curated_rows,
            quarantined_rows=quarantined_rows,
            unmatched_merchant_rows=unmatched_merchant_rows,
            amount_p99=amount_p99,
        )
    finally:
        connection.close()
