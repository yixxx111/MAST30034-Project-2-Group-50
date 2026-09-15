from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

import duckdb


PROFILE_NAME = "data_quality_profile.csv"


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _rate(count: int, denominator: int) -> float | None:
    return None if denominator == 0 else count / denominator


def _blank_or_null_sql(column: str, data_type: str) -> str:
    """Count blank strings as absent values, in addition to SQL NULLs."""

    if any(token in data_type.upper() for token in ("CHAR", "TEXT", "STRING")):
        return f'"{column}" IS NULL OR TRIM("{column}") = \'\''
    return f'"{column}" IS NULL'


def _write_rows(path: Path, rows: Iterable[dict[str, object]]) -> None:
    fieldnames = [
        "stage",
        "classification",
        "field_or_metric",
        "row_count",
        "rate_of_curated_rows",
        "treatment",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_data_quality_profile(output_root: str | Path = "data/curated") -> Path:
    """Write a field-level quality profile beside the curated parquet dataset."""

    root = Path(output_root).expanduser().resolve()
    curated_directory = root / "curated_transactions"
    quarantine_path = root / "quarantined_transactions.parquet"
    if not any(curated_directory.rglob("*.parquet")):
        raise FileNotFoundError(
            f"No curated parquet files found below {curated_directory}. "
            "Run src.run_pipeline first."
        )
    if not quarantine_path.exists():
        raise FileNotFoundError(
            f"Missing {quarantine_path}. Run src.run_pipeline first."
        )

    connection = duckdb.connect()
    try:
        curated_relation = (
            f"read_parquet('{_sql_path(curated_directory / '**' / '*.parquet')}', "
            "hive_partitioning = true)"
        )
        total_rows = int(
            connection.execute(f"SELECT COUNT(*) FROM {curated_relation}").fetchone()[0]
        )
        schema = connection.execute(f"DESCRIBE SELECT * FROM {curated_relation}").fetchall()
        rows: list[dict[str, object]] = []

        # Values that are absent in the fact table after curation.
        for column, data_type, *_ in schema:
            missing_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {curated_relation} "
                    f"WHERE {_blank_or_null_sql(column, data_type)}"
                ).fetchone()[0]
            )
            rows.append(
                {
                    "stage": "curated_transactions",
                    "classification": "field_missing_after_curation",
                    "field_or_metric": column,
                    "row_count": missing_count,
                    "rate_of_curated_rows": _rate(missing_count, total_rows),
                    "treatment": "Retain only when non-essential; inspect before downstream use.",
                    "notes": "Blank strings are counted as missing for text fields.",
                }
            )

        # Invalid/missing required input records appear in the quarantine rather
        # than being silently discarded from the audit trail.
        quarantine_relation = f"read_parquet('{_sql_path(quarantine_path)}')"
        quarantine_columns = {
            row[0]
            for row in connection.execute(
                f"DESCRIBE SELECT * FROM {quarantine_relation}"
            ).fetchall()
        }
        if "rejection_reason" in quarantine_columns:
            for reason, count in connection.execute(
                f"""
                SELECT COALESCE(rejection_reason, 'unspecified'), COUNT(*)
                FROM {quarantine_relation}
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall():
                count = int(count)
                rows.append(
                    {
                        "stage": "raw_transaction_validation",
                        "classification": "raw_invalid_or_missing",
                        "field_or_metric": reason,
                        "row_count": count,
                        "rate_of_curated_rows": _rate(count, total_rows),
                        "treatment": "Quarantined; excluded from curated_transactions.",
                        "notes": "This is a raw-data validity issue, not an imputed value.",
                    }
                )

        available_columns = {row[0] for row in schema}
        if "merchant_master_matched" in available_columns:
            unmatched_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {curated_relation} "
                    "WHERE COALESCE(merchant_master_matched, false) = false"
                ).fetchone()[0]
            )
            rows.append(
                {
                    "stage": "merchant_left_join",
                    "classification": "join_induced_unmatched",
                    "field_or_metric": "merchant_master_matched = false",
                    "row_count": unmatched_count,
                    "rate_of_curated_rows": _rate(unmatched_count, total_rows),
                    "treatment": "Retain the transaction and use the match flag in coverage decisions.",
                    "notes": "The merchant ABN existed in the transaction but was absent from the merchant master.",
                }
            )

        # Fraud and external features are deliberately not part of the core fact
        # table. Their availability must be audited after their own key/time and
        # cardinality checks, rather than defaulting unmatched records to zero.
        for metric, note in (
            (
                "consumer_fraud_probability",
                "Join only after validating keys, time coverage and one-to-one cardinality.",
            ),
            (
                "merchant_fraud_probability",
                "Join only after validating keys, time coverage and one-to-one cardinality.",
            ),
            (
                "external_geographic_features",
                "Join through a validated consumer-to-geography correspondence table.",
            ),
        ):
            rows.append(
                {
                    "stage": "pending_enrichment",
                    "classification": "not_integrated_not_a_missing_value",
                    "field_or_metric": metric,
                    "row_count": total_rows,
                    "rate_of_curated_rows": 1.0 if total_rows else None,
                    "treatment": "Do not impute or default to zero at this stage.",
                    "notes": note,
                }
            )

        profile_path = root / PROFILE_NAME
        _write_rows(profile_path, rows)
        return profile_path
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the curated-transaction field-level quality profile."
    )
    parser.add_argument("--output-root", default="data/curated")
    args = parser.parse_args()
    print(build_data_quality_profile(args.output_root))


if __name__ == "__main__":
    main()
