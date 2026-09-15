from __future__ import annotations

from pathlib import Path

import duckdb
import openpyxl
import pandas as pd
import pytest

from external_seifa.src.curation import (
    DataQualityError,
    build_features,
    consumer_coverage,
    enrich_rows,
    normalise_postcode,
    run_pipeline,
    sha256,
)
from external_seifa.src.enrich_transactions import enrich_transactions


DETAIL_HEADERS = [
    "2021 Postal Area (POA) Code",
    "Usual Resident Population",
    "Score",
    "",
    "Rank",
    "Decile",
    "Percentile",
    "",
    "State",
    "Rank",
    "Decile",
    "Percentile",
    "Minimum score for SA1s in area",
    "Maximum score for SA1s in area",
    "% Usual Resident Population without an SA1 level score",
    "Data should be used with caution - area not well represented by SA1s",
    "POA crosses state or territory boundaries",
]


def _detail_row(postcode: str, population: int, score: float, decile: int, percentile: int):
    return [postcode, population, score, None, percentile, decile, percentile, None, "VIC", percentile, decile, percentile, score - 20, score + 20, 0.01, None, None]


def make_workbook(path: Path) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name in ["Table 1", "Table 2", "Table 3", "Table 4", "Table 5", "Table 6"]:
        book.create_sheet(name)

    summary = book["Table 1"]
    summary.append([])
    for _ in range(4):
        summary.append([])
    summary.append([
        "2021 Postal Area (POA) Code", "Score", "Decile", "Score", "Decile", "Score", "Decile", "Score", "Decile",
        "Usual Resident Population", "Data should be used with caution - area not well represented by SA1s",
        "POA crosses state or territory boundaries",
    ])
    summary.append(["0800", 1000, 5, 1010, 6, 990, 4, 1020, 7, 1000, None, None])
    summary.append(["3000", 1100, 8, 1120, 9, 1050, 7, 1150, 9, 2000, None, None])
    summary.append(["2308", "-", "-", "-", "-", "-", "-", 900, 2, 100, None, None])

    values = {
        "Table 2": [("0800", 1000, 1000, 5, 50), ("3000", 2000, 1100, 8, 80)],
        "Table 3": [("0800", 1000, 1010, 6, 60), ("3000", 2000, 1120, 9, 90)],
        "Table 4": [("0800", 1000, 990, 4, 40), ("3000", 2000, 1050, 7, 70)],
        "Table 5": [("0800", 1000, 1020, 7, 70), ("3000", 2000, 1150, 9, 90), ("2308", 100, 900, 2, 20)],
    }
    for sheet_name, rows in values.items():
        sheet = book[sheet_name]
        for _ in range(5):
            sheet.append([])
        sheet.append(DETAIL_HEADERS)
        for row in rows:
            sheet.append(_detail_row(*row))

    excluded = book["Table 6"]
    for _ in range(5):
        excluded.append([])
    excluded.append(["2021 Postal Area (POA) Code", "Usual Resident Population", "IRSD", "IRSAD", "IER", "IEO"])
    excluded.append([2308, 100, "Y", "Y", "Y", "N"])
    excluded.append([9494, 50, "Y", "Y", "Y", "Y"])
    excluded.append([9797, 20, "Y", "Y", "Y", "Y"])
    book.save(path)
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return make_workbook(tmp_path / "seifa.xlsx")


def test_postcode_normalisation_rejects_decimal_text():
    result = normalise_postcode(pd.Series(["800", "0800", " 3000 ", "3000.0", "POA3000", None]))
    assert result.iloc[:3].tolist() == ["0800", "0800", "3000"]
    assert result.iloc[3:].isna().all()


def test_build_retains_ordinary_poa_without_all_scores(source: Path):
    clean, reports = build_features(source, expected_poa_count=5)
    assert clean["postcode"].tolist() == ["0800", "2308", "3000"]
    partial = clean.set_index("postcode").loc["2308"]
    assert partial["seifa_score_missing_count"] == 3
    assert partial["seifa_any_score_available"]
    assert not partial["seifa_all_scores_available"]
    assert len(reports["special_geography_records"]) == 2
    assert reports["summary_crosscheck"][["score_mismatches", "decile_mismatches"]].to_numpy().sum() == 0


def test_join_preserves_rows_and_separates_missing_score(source: Path):
    clean, _ = build_features(source, expected_poa_count=5)
    left = pd.DataFrame({"consumer_postcode": ["800", "2308", "9999", "9494", None], "id": range(5)})
    joined = enrich_rows(left, clean)
    assert joined["id"].tolist() == left["id"].tolist()
    assert joined["seifa_match_status"].tolist() == [
        "matched_all_scores",
        "matched_partial_scores",
        "postcode_not_in_seifa",
        "special_geography",
        "missing_or_invalid_postcode",
    ]


def test_consumer_coverage_reads_only_postcode(source: Path, tmp_path: Path):
    clean, _ = build_features(source, expected_poa_count=5)
    consumer = tmp_path / "consumer.csv"
    consumer.write_text("name|postcode|consumer_id\nPrivate|0800|1\nPerson|2308|2\nOther|9999|3\n", encoding="utf-8")
    coverage, _, exceptions, _ = consumer_coverage(consumer, clean)
    table = coverage.set_index(["scope", "metric"])
    assert table.loc[("consumer_rows", "matched_all_scores"), "rows"] == 1
    assert table.loc[("consumer_rows", "matched_partial_scores"), "rows"] == 1
    assert "name" not in exceptions and "consumer_id" not in exceptions


def test_pipeline_writes_typed_outputs_without_changing_source(source: Path, tmp_path: Path):
    before = sha256(source)
    output = tmp_path / "results"
    metadata = run_pipeline(source, output, expected_poa_count=5)
    assert metadata["clean_rows"] == 3
    assert sha256(source) == before
    parquet = pd.read_parquet(output / "seifa_clean.parquet")
    assert parquet["postcode"].tolist() == ["0800", "2308", "3000"]
    assert str(parquet["seifa_irsd_national_decile"].dtype) == "Int64"


def test_duplicate_dimension_is_rejected(source: Path):
    clean, _ = build_features(source, expected_poa_count=5)
    duplicated = pd.concat([clean, clean.iloc[[0]]], ignore_index=True)
    with pytest.raises(DataQualityError, match="unique"):
        enrich_rows(pd.DataFrame({"consumer_postcode": ["0800"]}), duplicated)


def test_transaction_enrichment_preserves_core_rows(source: Path, tmp_path: Path):
    results = tmp_path / "results"
    run_pipeline(source, results, expected_poa_count=5)
    transactions = pd.DataFrame(
        {
            "order_id": ["a", "b", "c"],
            "consumer_postcode": ["0800", "2308", "9999"],
            "dollar_value": [10.0, 20.0, 30.0],
            "merchant_master_matched": [True, False, True],
        }
    )
    source_transactions = tmp_path / "transactions.parquet"
    transactions.to_parquet(source_transactions, index=False)
    before = sha256(source_transactions)
    metadata = enrich_transactions(source_transactions, results / "seifa_clean.parquet", tmp_path / "enriched")
    enriched = pd.read_parquet(tmp_path / "enriched" / "curated_transactions_with_seifa.parquet").sort_values("order_id")
    assert metadata["input_rows"] == metadata["output_rows"] == 3
    assert enriched["merchant_master_matched"].tolist() == [True, False, True]
    assert enriched["seifa_match_status"].tolist() == ["matched_all_scores", "matched_partial_scores", "postcode_not_in_seifa"]
    assert sha256(source_transactions) == before
