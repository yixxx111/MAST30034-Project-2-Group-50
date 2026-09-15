from __future__ import annotations

import csv

import duckdb

from src.data_quality import build_data_quality_profile


def _copy_query_to_parquet(database: duckdb.DuckDBPyConnection, query: str, path) -> None:
    escaped_path = str(path).replace("'", "''")
    database.execute(f"COPY ({query}) TO '{escaped_path}' (FORMAT PARQUET)")


def test_profile_distinguishes_join_missingness_from_pending_enrichment(tmp_path):
    output_root = tmp_path / "curated"
    partition = output_root / "curated_transactions" / "order_year=2022" / "order_month=01"
    partition.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        _copy_query_to_parquet(
            connection,
            """
            SELECT * FROM (
                VALUES
                    ('order-1', 'merchant-1', true, 'Merchant One', 'NSW'),
                    ('order-2', 'merchant-x', false, NULL, '')
            ) AS records(order_id, merchant_abn, merchant_master_matched, merchant_name, consumer_state)
            """,
            partition / "part-000.parquet",
        )
        _copy_query_to_parquet(
            connection,
            "SELECT 'missing_order_id' AS rejection_reason",
            output_root / "quarantined_transactions.parquet",
        )
    finally:
        connection.close()

    profile_path = build_data_quality_profile(output_root)
    with profile_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    merchant_row = next(
        row
        for row in rows
        if row["classification"] == "join_induced_unmatched"
        and row["field_or_metric"] == "merchant_master_matched = false"
    )
    assert merchant_row["row_count"] == "1"
    assert merchant_row["rate_of_curated_rows"] == "0.5"

    raw_row = next(
        row
        for row in rows
        if row["classification"] == "raw_invalid_or_missing"
        and row["field_or_metric"] == "missing_order_id"
    )
    assert raw_row["row_count"] == "1"

    pending_row = next(
        row
        for row in rows
        if row["field_or_metric"] == "consumer_fraud_probability"
    )
    assert pending_row["classification"] == "not_integrated_not_a_missing_value"
