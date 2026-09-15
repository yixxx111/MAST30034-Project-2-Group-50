"""Append SEIFA POA features to a copy of Member 2's curated Parquet output."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .curation import DataQualityError, SPECIAL, _publish_directory, _validate_output, sha256


def _quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def enrich_transactions(transactions: str | Path, seifa_parquet: str | Path, output_root: str | Path) -> dict:
    transactions, seifa_parquet, output_root = [Path(p).resolve() for p in (transactions, seifa_parquet, output_root)]
    files = sorted(transactions.rglob("*.parquet")) if transactions.is_dir() else [transactions]
    if not files or any(not file.is_file() for file in files):
        raise FileNotFoundError("No input transaction Parquet files")
    _validate_output(output_root, [transactions, seifa_parquet], marker_name="enrichment_metadata.json")
    if transactions.is_dir() and transactions in output_root.parents:
        raise DataQualityError("Enriched output must be outside the transaction input directory")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".seifa-enrichment-", dir=output_root.parent))
    try:
        with duckdb.connect() as connection:
            connection.read_parquet([str(p) for p in files], hive_partitioning=True).create_view("source_transactions")
            connection.read_parquet(str(seifa_parquet)).create_view("seifa")
            transaction_columns = [row[0] for row in connection.execute("DESCRIBE source_transactions").fetchall()]
            seifa_columns = [row[0] for row in connection.execute("DESCRIBE seifa").fetchall()]
            required = {"order_id", "consumer_postcode"}
            if not required.issubset(transaction_columns):
                raise DataQualityError(f"Missing core fields: {sorted(required - set(transaction_columns))}")
            if not {"postcode", "poa_code", "seifa_source_year"}.issubset(seifa_columns):
                raise DataQualityError("Input is not a SEIFA postcode dimension")
            feature_columns = [c for c in seifa_columns if c.startswith("seifa_")]
            reserved = feature_columns + ["seifa_postcode", "seifa_poa_code", "seifa_matched", "seifa_match_status"]
            if set(reserved) & set(transaction_columns):
                raise DataQualityError("Transactions already contain SEIFA enrichment columns")
            bad, rows, unique_rows = connection.execute(
                """SELECT
                       count(*) FILTER (WHERE postcode IS NULL
                           OR NOT regexp_full_match(CAST(postcode AS VARCHAR), '[0-9]{4}')
                           OR postcode IN ('9494','9797')),
                       count(*), count(DISTINCT postcode)
                   FROM seifa"""
            ).fetchone()
            if not rows or bad or rows != unique_rows:
                raise DataQualityError("SEIFA dimension has invalid or duplicate postcode keys")
            before, unique_orders = connection.execute(
                "SELECT count(*), count(DISTINCT order_id) FROM source_transactions"
            ).fetchone()
            if before != unique_orders:
                raise DataQualityError("Input must have a unique non-null order_id")

            features_sql = ", ".join("s." + _identifier(c) for c in feature_columns)
            connection.execute(
                f"""CREATE TEMP VIEW enriched AS
                    WITH normalised AS (
                        SELECT *, CASE
                            WHEN regexp_full_match(trim(CAST(consumer_postcode AS VARCHAR)), '[0-9]{{1,4}}')
                            THEN lpad(trim(CAST(consumer_postcode AS VARCHAR)), 4, '0')
                            ELSE NULL END AS seifa_postcode
                        FROM source_transactions
                    )
                    SELECT t.*, s.poa_code AS seifa_poa_code, {features_sql},
                           s.postcode IS NOT NULL AS seifa_matched,
                           CASE
                               WHEN t.seifa_postcode IS NULL THEN 'missing_or_invalid_postcode'
                               WHEN t.seifa_postcode IN ('9494','9797') THEN 'special_geography'
                               WHEN s.postcode IS NULL THEN 'postcode_not_in_seifa'
                               WHEN s.seifa_all_scores_available THEN 'matched_all_scores'
                               WHEN s.seifa_any_score_available THEN 'matched_partial_scores'
                               ELSE 'matched_no_scores'
                           END AS seifa_match_status
                    FROM normalised t
                    LEFT JOIN seifa s ON t.seifa_postcode = s.postcode"""
            )
            after = connection.execute("SELECT count(*) FROM enriched").fetchone()[0]
            if before != after:
                raise DataQualityError("SEIFA join changed transaction count")

            amount_sql = ", sum(dollar_value) AS dollar_value" if "dollar_value" in transaction_columns else ""
            coverage = connection.execute(
                f"""SELECT seifa_match_status, count(*) AS transaction_rows,
                           count(*)::DOUBLE / NULLIF({before}, 0) AS transaction_rate {amount_sql}
                    FROM enriched GROUP BY seifa_match_status ORDER BY seifa_match_status"""
            ).df()
            if "dollar_value" in coverage:
                total_value = coverage["dollar_value"].sum()
                coverage["dollar_value_rate"] = coverage["dollar_value"] / total_value if total_value else np.nan
            coverage.to_csv(stage / "transaction_join_coverage.csv", index=False)

            missing_sql = ",".join(f"count(*) FILTER (WHERE {_identifier(f)} IS NULL)" for f in feature_columns)
            matched_missing_sql = ",".join(
                f"count(*) FILTER (WHERE seifa_matched AND {_identifier(f)} IS NULL)" for f in feature_columns
            )
            totals = connection.execute(f"SELECT {missing_sql}, {matched_missing_sql} FROM enriched").fetchone()
            pd.DataFrame(
                [
                    {
                        "field": field,
                        "all_transaction_missing": totals[i],
                        "matched_geography_missing": totals[len(feature_columns) + i],
                    }
                    for i, field in enumerate(feature_columns)
                ]
            ).to_csv(stage / "transaction_feature_missingness.csv", index=False)
            connection.execute(
                f"""COPY (
                        SELECT seifa_postcode, seifa_match_status, count(*) AS transaction_rows
                        FROM enriched WHERE seifa_match_status <> 'matched_all_scores' GROUP BY ALL
                    ) TO {_quote(stage / 'transaction_postcode_exceptions.csv')} (HEADER, DELIMITER ',')"""
            )
            output = stage / "curated_transactions_with_seifa.parquet"
            connection.execute(f"COPY enriched TO {_quote(output)} (FORMAT PARQUET, COMPRESSION ZSTD)")
            saved = connection.read_parquet(str(output)).aggregate("count(*)").fetchone()[0]
            if saved != before:
                raise DataQualityError("Saved output row count differs from input")

        metadata = {
            "stage": "seifa_transaction_enrichment",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input_rows": before,
            "output_rows": after,
            "input_partition_count": len(files),
            "seifa_sha256": sha256(seifa_parquet),
            "join": "normalised consumer_postcode, many-to-one LEFT JOIN",
            "source_year": 2021,
            "base_modified": False,
            "output": "curated_transactions_with_seifa.parquet",
        }
        (stage / "enrichment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        _publish_directory(stage, output_root)
        return metadata
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Append SEIFA features to a copy of curated transactions.")
    parser.add_argument("--transactions", required=True, type=Path)
    parser.add_argument("--seifa-parquet", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(enrich_transactions(args.transactions, args.seifa_parquet, args.output_root), indent=2))


if __name__ == "__main__":
    main()
