from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from src.curation import DataQualityError, build_curated_transactions


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _write_parquet(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: str,
    rows: list[tuple],
    path: Path,
) -> None:
    connection.execute(f"CREATE TABLE {table_name} ({columns})")
    placeholders = ", ".join("?" for _ in rows[0])
    connection.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY {table_name} TO '{_sql_path(path)}' (FORMAT PARQUET)")


def _write_tables(
    data_root: Path,
    transaction_partitions: dict[str, list[tuple]],
    user_rows: list[tuple] | None = None,
    consumer_rows: list[tuple] | None = None,
    merchant_rows: list[tuple] | None = None,
) -> None:

    data_root.mkdir(parents=True, exist_ok=True)
    user_rows = user_rows or [(10, 1), (20, 2)]
    consumer_rows = consumer_rows or [
        ("Alice", "One Example St", " vic ", 862, "female", 1),
        ("Bob", "Two Example St", "NSW", 3000, "male", 2),
    ]
    merchant_rows = merchant_rows or [("Known merchant", "[(retail), (a), (take rate: 5)]", 100)]

    with (data_root / "tbl_consumer.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["name", "address", "state", "postcode", "gender", "consumer_id"])
        writer.writerows(consumer_rows)

    connection = duckdb.connect()
    try:
        _write_parquet(
            connection,
            "user_map",
            "user_id BIGINT, consumer_id BIGINT",
            user_rows,
            data_root / "consumer_user_details.parquet",
        )
        _write_parquet(
            connection,
            "merchants",
            "name VARCHAR, tags VARCHAR, merchant_abn BIGINT",
            merchant_rows,
            data_root / "tbl_merchants.parquet",
        )
        for index, (partition_date, rows) in enumerate(transaction_partitions.items()):
            _write_parquet(
                connection,
                f"transactions_{index}",
                "user_id BIGINT, merchant_abn BIGINT, dollar_value DOUBLE, order_id VARCHAR",
                rows,
                data_root
                / "transactions_20220228_20220828_snapshot"
                / f"order_datetime={partition_date}"
                / "part-00000.parquet",
            )
    finally:
        connection.close()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def test_curation_quarantines_invalid_records_and_retains_unmatched_merchants(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "tables"
    _write_tables(
        data_root,
        {
            "2021-02-28": [
                (10, 100, 12.50, "order-1"),
                (10, 100, 12.50, "order-1"), 
                (10, 100, 20.00, "conflicting-order"),
                (10, 100, 25.00, "conflicting-order"),
                (20, 999, 50.00, "unmatched-merchant"),
                (10, 100, 0.00, "zero-value"),
                (10, None, 25.00, "missing-merchant"),
                (20, 100, 600.00, "high-value"),
            ]
        },
    )

    output_root = tmp_path / "output"
    result = build_curated_transactions(data_root, output_root)

    assert result.input_rows == 8
    assert result.curated_rows == 3
    assert result.quarantined_rows == 5
    assert result.unmatched_merchant_rows == 1

    connection = duckdb.connect()
    try:
        curated = connection.execute(
            f"SELECT * FROM read_parquet('{_sql_path(result.curated_transactions_path / '**/*.parquet')}', hive_partitioning = true)"
        ).fetchdf()
        quarantine = connection.execute(
            f"SELECT * FROM read_parquet('{_sql_path(result.quarantine_path)}')"
        ).fetchdf()
    finally:
        connection.close()

    assert set(curated["order_id"]) == {"order-1", "unmatched-merchant", "high-value"}
    assert curated.loc[curated["order_id"] == "unmatched-merchant", "merchant_master_matched"].item() is False
    assert curated.loc[curated["order_id"] == "order-1", "consumer_postcode"].item() == "0862"
    assert curated.loc[curated["order_id"] == "order-1", "consumer_state"].item() == "VIC"
    assert curated.loc[curated["order_id"] == "high-value", "is_amount_above_p99"].item() is True
    assert "name" not in curated.columns
    assert "address" not in curated.columns
    assert set(quarantine["rejection_reason"]) == {
        "exact_duplicate_order_id",
        "conflicting_duplicate_order_id",
        "non_positive_dollar_value",
        "missing_merchant_abn",
    }

    exception_rows = _read_csv(result.merchant_exceptions_path)
    assert exception_rows == [
        {
            "merchant_abn": "999",
            "transaction_count": "1",
            "total_dollar_value": "50.0",
            "first_order_datetime": "2021-02-28",
            "last_order_datetime": "2021-02-28",
        }
    ]
    audit = _read_csv(result.audit_path)
    assert [row["stage"] for row in audit] == [
        "raw_transaction_partitions",
        "basic_validation",
        "duplicate_order_resolution",
        "consumer_internal_joins",
        "merchant_left_join_and_output",
    ]

    second_result = build_curated_transactions(data_root, output_root)
    assert second_result.curated_rows == result.curated_rows
    assert second_result.quarantined_rows == result.quarantined_rows


def test_invalid_partition_date_is_quarantined(tmp_path: Path) -> None:
    data_root = tmp_path / "tables"
    _write_tables(
        data_root,
        {
            "2021-02-28": [(10, 100, 10.00, "valid-order")],
            "not-a-date": [(10, 100, 10.00, "invalid-date-order")],
        },
    )

    result = build_curated_transactions(data_root, tmp_path / "output")
    assert result.curated_rows == 1
    connection = duckdb.connect()
    try:
        reasons = connection.execute(
            f"SELECT rejection_reason FROM read_parquet('{_sql_path(result.quarantine_path)}')"
        ).fetchall()
    finally:
        connection.close()
    assert reasons == [("invalid_order_datetime",)]


def test_duplicate_dimension_key_fails_before_join_expansion(tmp_path: Path) -> None:
    data_root = tmp_path / "tables"
    _write_tables(
        data_root,
        {"2021-02-28": [(10, 100, 10.00, "valid-order")]},
        merchant_rows=[
            ("Known merchant", "[(retail), (a), (take rate: 5)]", 100),
            ("Duplicate merchant", "[(retail), (a), (take rate: 5)]", 100),
        ],
    )

    with pytest.raises(DataQualityError, match="merchants.merchant_abn"):
        build_curated_transactions(data_root, tmp_path / "output")
