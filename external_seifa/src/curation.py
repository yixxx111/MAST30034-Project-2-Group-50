"""Clean the official ABS SEIFA 2021 Postal Area workbook.

The output is one row per ordinary 2021 POA. Areas without an index score are
retained so a later join can distinguish an unavailable score from an unknown
postcode. Raw inputs are read-only and are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data_quality import profile_features, range_issues, score_correlations, score_distribution


SOURCE_URL = (
    "https://www.abs.gov.au/statistics/people/people-and-communities/"
    "socio-economic-indexes-areas-seifa-australia/2021/"
    "Postal%20Area%2C%20Indexes%2C%20SEIFA%202021.xlsx"
)
METHODOLOGY_URL = (
    "https://www.abs.gov.au/methodologies/"
    "socio-economic-indexes-areas-seifa-australia-methodology/2021"
)
INDEX_SHEETS = {
    "irsd": "Table 2",
    "irsad": "Table 3",
    "ier": "Table 4",
    "ieo": "Table 5",
}
INDEX_NAMES = {
    "irsd": "Index of Relative Socio-economic Disadvantage",
    "irsad": "Index of Relative Socio-economic Advantage and Disadvantage",
    "ier": "Index of Economic Resources",
    "ieo": "Index of Education and Occupation",
}
SPECIAL = {"9494": "no_usual_address", "9797": "migratory_offshore_shipping"}
VALID_STATES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT", "Cross Border"}
DETAIL_COLUMNS = [
    "postcode",
    "population",
    "score",
    "separator_1",
    "national_rank",
    "national_decile",
    "national_percentile",
    "separator_2",
    "state",
    "state_rank",
    "state_decile",
    "state_percentile",
    "minimum_sa1_score",
    "maximum_sa1_score",
    "population_without_sa1_score_share",
    "caution_low_sa1_representation",
    "cross_state_boundary",
]


class DataQualityError(ValueError):
    """Stop when schema, keys, or row-preservation contracts are unsafe."""


def _ascii_postcode_mask(strings: pd.Series) -> pd.Series:
    """Avoid backend-specific vectorised-regex edge cases with nullable strings."""
    return strings.map(
        lambda value: False if pd.isna(value) else re.fullmatch(r"[0-9]{1,4}", str(value)) is not None,
        na_action=None,
    ).astype(bool)


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_postcode(values: pd.Series) -> pd.Series:
    """Trim 1-4 ASCII digits and left-pad; ambiguous decimal text is rejected."""
    strings = values.astype("string").str.strip()
    return strings.where(_ascii_postcode_mask(strings)).str.zfill(4)


def _normalise_source_poa(values: pd.Series) -> pd.Series:
    """Accept Excel numeric POA cells while still rejecting non-integral values."""
    strings = values.astype("string").str.strip()
    direct = strings.where(_ascii_postcode_mask(strings)).str.zfill(4)
    numeric = pd.to_numeric(values, errors="coerce")
    integral = numeric.notna() & np.isfinite(numeric) & numeric.between(0, 9999) & numeric.mod(1).eq(0)
    numeric_codes = numeric.where(integral).astype("Int64").astype("string").str.zfill(4)
    return direct.fillna(numeric_codes)


def _read_sheet(path: Path, sheet: str, usecols: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet, header=5, usecols=usecols, dtype=object)
    except ValueError as exc:
        raise DataQualityError(f"{sheet}: workbook schema could not be read: {exc}") from exc


def _valid_data_rows(frame: pd.DataFrame, key_column: str) -> pd.DataFrame:
    keys = _normalise_source_poa(frame[key_column])
    data = frame.loc[keys.notna()].copy()
    data[key_column] = keys.loc[keys.notna()]
    if data[key_column].duplicated().any():
        examples = data.loc[data[key_column].duplicated(keep=False), key_column].unique()[:5].tolist()
        raise DataQualityError(f"Duplicate POA keys after normalisation: {examples}")
    return data


def read_summary(path: Path) -> pd.DataFrame:
    raw = _read_sheet(path, "Table 1", "A:L")
    if len(raw.columns) != 12:
        raise DataQualityError(f"Table 1: expected 12 columns, found {len(raw.columns)}")
    raw.columns = [
        "postcode",
        "irsd_score",
        "irsd_decile",
        "irsad_score",
        "irsad_decile",
        "ier_score",
        "ier_decile",
        "ieo_score",
        "ieo_decile",
        "population",
        "caution",
        "cross_state",
    ]
    data = _valid_data_rows(raw, "postcode")
    for column in [c for c in data.columns if c not in {"postcode", "caution", "cross_state"}]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["caution"] = data["caution"].eq("Y")
    data["cross_state"] = data["cross_state"].eq("Y")
    return data.reset_index(drop=True)


def read_excluded(path: Path) -> pd.DataFrame:
    raw = _read_sheet(path, "Table 6", "A:F")
    if len(raw.columns) != 6:
        raise DataQualityError(f"Table 6: expected 6 columns, found {len(raw.columns)}")
    raw.columns = ["postcode", "population", "irsd", "irsad", "ier", "ieo"]
    data = _valid_data_rows(raw, "postcode")
    data["population"] = pd.to_numeric(data["population"], errors="coerce").astype("Int64")
    for index in INDEX_SHEETS:
        unexpected = ~data[index].isin(["Y", "N"])
        if unexpected.any():
            raise DataQualityError(f"Table 6: unexpected exclusion flag in {index}")
        data[f"seifa_{index}_excluded"] = data[index].eq("Y")
    data["reason"] = data["postcode"].map(SPECIAL).fillna("ordinary_poa_without_one_or_more_index_scores")
    data["special_geography"] = data["postcode"].isin(SPECIAL)
    keep = ["postcode", "population"] + [f"seifa_{x}_excluded" for x in INDEX_SHEETS] + ["reason", "special_geography"]
    return data[keep].reset_index(drop=True)


def read_index_detail(path: Path, index: str, sheet: str) -> tuple[pd.DataFrame, dict]:
    raw = _read_sheet(path, sheet, "A:Q")
    if len(raw.columns) != len(DETAIL_COLUMNS):
        raise DataQualityError(f"{sheet}: expected {len(DETAIL_COLUMNS)} columns, found {len(raw.columns)}")
    raw.columns = DETAIL_COLUMNS
    data = _valid_data_rows(raw, "postcode")
    data = data.drop(columns=["separator_1", "separator_2"])

    numeric = [
        "population",
        "score",
        "national_rank",
        "national_decile",
        "national_percentile",
        "state_rank",
        "state_decile",
        "state_percentile",
        "minimum_sa1_score",
        "maximum_sa1_score",
        "population_without_sa1_score_share",
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["population"] = data["population"].astype("Int64")
    for column in ["national_rank", "national_decile", "national_percentile", "state_rank", "state_decile", "state_percentile"]:
        data[column] = data[column].astype("Int64")

    bad_state = data["state"].notna() & ~data["state"].isin(VALID_STATES)
    if bad_state.any():
        raise DataQualityError(f"{sheet}: unexpected state values {data.loc[bad_state, 'state'].unique().tolist()}")
    for column in ["caution_low_sa1_representation", "cross_state_boundary"]:
        bad_flag = data[column].notna() & data[column].ne("Y")
        if bad_flag.any():
            raise DataQualityError(f"{sheet}: unexpected values in {column}")
        data[column] = data[column].eq("Y")

    if (data["minimum_sa1_score"] > data["maximum_sa1_score"]).any():
        raise DataQualityError(f"{sheet}: minimum SA1 score exceeds maximum")

    support = data[["postcode", "population", "state", "caution_low_sa1_representation", "cross_state_boundary"]].copy()
    detail = data.drop(columns=["population", "state", "caution_low_sa1_representation", "cross_state_boundary"])
    detail = detail.rename(columns={c: f"seifa_{index}_{c}" for c in detail.columns if c != "postcode"})
    audit = {
        "index": index.upper(),
        "sheet": sheet,
        "data_rows": len(data),
        "unique_postcodes": int(data["postcode"].nunique()),
        "score_rows": int(data["score"].notna().sum()),
        "state_rank_missing_rows": int(data["state_rank"].isna().sum()),
        "caution_rows": int(data["caution_low_sa1_representation"].sum()),
        "cross_state_rows": int(data["cross_state_boundary"].sum()),
    }
    support = support.rename(columns={c: f"_{index}_{c}" for c in support.columns if c != "postcode"})
    return detail.merge(support, on="postcode", validate="one_to_one"), audit


def _first_nonmissing(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    return frame[columns].bfill(axis=1).iloc[:, 0]


def _nullable_any(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    source = frame[columns].astype("boolean")
    result = source.any(axis=1).astype("boolean")
    result.loc[source.isna().all(axis=1)] = pd.NA
    return result


def build_features(path: str | Path, expected_poa_count: int | None = 2643) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    summary = read_summary(path)
    excluded = read_excluded(path)
    details: dict[str, pd.DataFrame] = {}
    audits = []
    for index, sheet in INDEX_SHEETS.items():
        details[index], audit = read_index_detail(path, index, sheet)
        audits.append(audit)

    universe = sorted(set(summary["postcode"]).union(excluded["postcode"]))
    if expected_poa_count is not None and len(universe) != expected_poa_count:
        raise DataQualityError(
            f"Expected {expected_poa_count:,} published POAs including special geographies, found {len(universe)}"
        )
    combined = pd.DataFrame({"postcode": universe})
    for index in INDEX_SHEETS:
        combined = combined.merge(details[index], on="postcode", how="left", validate="one_to_one")

    summary_population = summary.set_index("postcode")["population"]
    excluded_population = excluded.set_index("postcode")["population"]
    combined["seifa_population"] = combined["postcode"].map(summary_population).fillna(
        combined["postcode"].map(excluded_population)
    ).astype("Int64")

    population_columns = [f"_{index}_population" for index in INDEX_SHEETS]
    for column in population_columns:
        mismatch = combined[column].notna() & combined["seifa_population"].notna() & combined[column].ne(combined["seifa_population"])
        if mismatch.any():
            raise DataQualityError(f"Population differs between summary and {column}")

    state_columns = [f"_{index}_state" for index in INDEX_SHEETS]
    combined["seifa_state"] = _first_nonmissing(combined, state_columns).astype("string")
    caution_columns = [f"_{index}_caution_low_sa1_representation" for index in INDEX_SHEETS]
    cross_columns = [f"_{index}_cross_state_boundary" for index in INDEX_SHEETS]
    combined["seifa_caution_low_sa1_representation"] = _nullable_any(combined, caution_columns)
    combined["seifa_cross_state_boundary"] = _nullable_any(combined, cross_columns)

    exclusion_map = excluded.set_index("postcode")
    for index in INDEX_SHEETS:
        flag = combined["postcode"].map(exclusion_map[f"seifa_{index}_excluded"])
        combined[f"seifa_{index}_excluded"] = flag.fillna(False).astype("boolean")
        combined[f"seifa_{index}_national_percentile_scaled"] = (
            combined[f"seifa_{index}_national_percentile"].astype("Float64") / 100.0
        )

    score_columns = [f"seifa_{index}_score" for index in INDEX_SHEETS]
    combined["seifa_score_missing_count"] = combined[score_columns].isna().sum(axis=1).astype("Int64")
    combined["seifa_any_score_available"] = combined[score_columns].notna().any(axis=1)
    combined["seifa_all_scores_available"] = combined[score_columns].notna().all(axis=1)
    combined["seifa_any_index_excluded"] = combined[[f"seifa_{i}_excluded" for i in INDEX_SHEETS]].any(axis=1)
    combined["seifa_source_year"] = 2021
    combined.insert(0, "poa_code", "POA" + combined["postcode"])

    support_columns = population_columns + state_columns + caution_columns + cross_columns
    combined = combined.drop(columns=support_columns)
    special_records = combined[combined["postcode"].isin(SPECIAL)].copy()
    clean = combined[~combined["postcode"].isin(SPECIAL)].copy().sort_values("postcode").reset_index(drop=True)
    if clean["postcode"].duplicated().any() or clean["postcode"].isna().any():
        raise DataQualityError("Clean postcode key must be unique and non-null")

    crosschecks = []
    for index in INDEX_SHEETS:
        check = summary[["postcode", f"{index}_score", f"{index}_decile"]].merge(
            clean[["postcode", f"seifa_{index}_score", f"seifa_{index}_national_decile"]],
            on="postcode",
            how="inner",
        )
        score_match = np.isclose(
            pd.to_numeric(check[f"{index}_score"], errors="coerce"),
            pd.to_numeric(check[f"seifa_{index}_score"], errors="coerce"),
            equal_nan=True,
        )
        decile_match = (
            pd.to_numeric(check[f"{index}_decile"], errors="coerce").astype("Float64")
            == pd.to_numeric(check[f"seifa_{index}_national_decile"], errors="coerce").astype("Float64")
        ).fillna(
            pd.to_numeric(check[f"{index}_decile"], errors="coerce").isna()
            & pd.to_numeric(check[f"seifa_{index}_national_decile"], errors="coerce").isna()
        )
        crosschecks.append(
            {
                "index": index.upper(),
                "compared_rows": len(check),
                "score_mismatches": int((~score_match).sum()),
                "decile_mismatches": int((~decile_match).sum()),
            }
        )

    score_issues = range_issues(clean)
    reports = {
        "source_audit": pd.DataFrame(audits),
        "excluded_poa_records": excluded,
        "special_geography_records": special_records,
        "summary_crosscheck": pd.DataFrame(crosschecks),
        "numeric_issues": score_issues,
        "feature_quality_profile": profile_features(clean),
        "score_distribution": score_distribution(clean, score_columns),
        "score_correlations": score_correlations(clean, score_columns),
    }
    return clean, reports


def enrich_rows(frame: pd.DataFrame, features: pd.DataFrame, postcode_column: str = "consumer_postcode") -> pd.DataFrame:
    if postcode_column not in frame:
        raise DataQualityError(f"Missing {postcode_column}")
    if features["postcode"].isna().any() or features["postcode"].duplicated().any():
        raise DataQualityError("SEIFA postcode dimension must be non-null and unique")
    if features["postcode"].isin(SPECIAL).any():
        raise DataQualityError("Special POAs must not enter the join dimension")
    feature_columns = [c for c in features if c.startswith("seifa_")]
    reserved = feature_columns + ["seifa_postcode", "seifa_poa_code", "seifa_matched", "seifa_match_status"]
    if set(reserved) & set(frame):
        raise DataQualityError("Input already contains SEIFA enrichment columns")

    left = frame.copy()
    left["seifa_postcode"] = normalise_postcode(left[postcode_column])
    right = features[["postcode", "poa_code"] + feature_columns].rename(
        columns={"postcode": "seifa_postcode", "poa_code": "seifa_poa_code"}
    )
    joined = left.merge(right, on="seifa_postcode", how="left", validate="many_to_one", indicator="_seifa_join", sort=False)
    if len(joined) != len(frame):
        raise DataQualityError("SEIFA join changed row count")
    joined["seifa_matched"] = joined.pop("_seifa_join").eq("both")
    all_scores = joined["seifa_all_scores_available"].fillna(False).astype(bool)
    any_score = joined["seifa_any_score_available"].fillna(False).astype(bool)
    joined["seifa_match_status"] = "postcode_not_in_seifa"
    joined.loc[joined["seifa_matched"] & all_scores, "seifa_match_status"] = "matched_all_scores"
    joined.loc[joined["seifa_matched"] & ~all_scores & any_score, "seifa_match_status"] = "matched_partial_scores"
    joined.loc[joined["seifa_matched"] & ~any_score, "seifa_match_status"] = "matched_no_scores"
    joined.loc[joined["seifa_postcode"].isin(SPECIAL), "seifa_match_status"] = "special_geography"
    joined.loc[joined["seifa_postcode"].isna(), "seifa_match_status"] = "missing_or_invalid_postcode"
    return joined


def consumer_coverage(path: str | Path, features: pd.DataFrame, postcode_column: str = "postcode"):
    path = Path(path)
    with path.open(encoding="utf-8-sig") as handle:
        first_line = handle.readline()
    separator = "|" if "|" in first_line else ","
    consumers = pd.read_csv(path, sep=separator, usecols=[postcode_column], dtype="string", keep_default_na=False)
    joined = enrich_rows(consumers, features, postcode_column)
    total = len(joined)
    statuses = [
        "matched_all_scores",
        "matched_partial_scores",
        "matched_no_scores",
        "postcode_not_in_seifa",
        "special_geography",
        "missing_or_invalid_postcode",
    ]
    coverage = []
    for status in statuses:
        count = int(joined["seifa_match_status"].eq(status).sum())
        coverage.append(
            {"scope": "consumer_rows", "metric": status, "rows": count, "denominator": total, "rate": count / total if total else None}
        )
    valid_unique = joined["seifa_postcode"].dropna().nunique()
    matched_unique = joined.loc[joined["seifa_matched"], "seifa_postcode"].nunique()
    coverage.append(
        {
            "scope": "distinct_valid_postcodes",
            "metric": "geography_matched",
            "rows": matched_unique,
            "denominator": valid_unique,
            "rate": matched_unique / valid_unique if valid_unique else None,
        }
    )

    missing = []
    for field in [c for c in features if c.startswith("seifa_")]:
        missing.append(
            {
                "field": field,
                "all_consumer_missing": int(joined[field].isna().sum()),
                "matched_geography_missing": int(joined.loc[joined["seifa_matched"], field].isna().sum()),
                "unmatched_consumer_rows": int((~joined["seifa_matched"]).sum()),
                "all_consumer_rows": total,
            }
        )
    counts = joined.groupby(["seifa_postcode", "seifa_match_status"], dropna=False).size().reset_index(name="consumer_rows")
    exceptions = counts[~counts["seifa_match_status"].eq("matched_all_scores")].copy()
    return pd.DataFrame(coverage), pd.DataFrame(missing), exceptions, counts


def data_dictionary(features: pd.DataFrame) -> pd.DataFrame:
    descriptions = {
        "poa_code": ("ABS 2021 Postal Area key", "string", "derived from postcode"),
        "postcode": ("Four-character postcode-compatible POA key", "string", "ABS POA code"),
        "seifa_population": ("Usual resident population", "persons", "ABS SEIFA summary / excluded areas"),
        "seifa_state": ("State, territory, Other Territories or Cross Border", "category", "ABS index tables"),
        "seifa_caution_low_sa1_representation": ("ABS caution flag: area not well represented by SA1s", "boolean", "ABS index tables"),
        "seifa_cross_state_boundary": ("POA crosses a state or territory boundary", "boolean", "ABS index tables"),
        "seifa_score_missing_count": ("Number of the four SEIFA index scores unavailable", "count 0-4", "derived"),
        "seifa_any_score_available": ("At least one SEIFA index score is available", "boolean", "derived"),
        "seifa_all_scores_available": ("All four SEIFA index scores are available", "boolean", "derived"),
        "seifa_any_index_excluded": ("ABS excluded at least one index for this POA", "boolean", "Table 6"),
        "seifa_source_year": ("SEIFA reference year", "year", "constant 2021"),
    }
    rows = []
    for field in features.columns:
        if field in descriptions:
            definition, unit, source = descriptions[field]
        else:
            index = next((i for i in INDEX_SHEETS if field.startswith(f"seifa_{i}_")), None)
            suffix = field.removeprefix(f"seifa_{index}_") if index else field
            source = f"ABS {INDEX_NAMES[index]} table" if index else "derived"
            if suffix == "score":
                definition, unit = "ABS standardised index score; lower means relatively more disadvantaged", "score"
            elif suffix in {"national_rank", "state_rank"}:
                definition, unit = f"{suffix.replace('_', ' ').title()}; low rank means lower index score", "rank"
            elif suffix in {"national_decile", "state_decile"}:
                definition, unit = f"{suffix.replace('_', ' ').title()}; 1 is lowest, 10 highest", "integer 1-10"
            elif suffix in {"national_percentile", "state_percentile"}:
                definition, unit = f"{suffix.replace('_', ' ').title()}; 1 is lowest, 100 highest", "integer 1-100"
            elif suffix == "national_percentile_scaled":
                definition, unit = "National percentile divided by 100; higher means less disadvantage / more advantage", "proportion 0-1"
                source = f"seifa_{index}_national_percentile / 100"
            elif suffix == "minimum_sa1_score":
                definition, unit = "Minimum contributing SA1 index score", "score"
            elif suffix == "maximum_sa1_score":
                definition, unit = "Maximum contributing SA1 index score", "score"
            elif suffix == "population_without_sa1_score_share":
                definition, unit = "Share of usual resident population without an SA1-level score", "proportion 0-1"
            elif suffix == "excluded":
                definition, unit = "ABS did not publish this index score for the POA", "boolean"
            else:
                definition, unit = suffix.replace("_", " ").title(), ""
        rows.append({"field": field, "source": source, "definition": definition, "unit": unit})
    return pd.DataFrame(rows)


def _validate_output(target: Path, inputs: list[Path], marker_name: str = "seifa_metadata.json") -> None:
    if any(source == target or target in source.parents for source in inputs):
        raise DataQualityError("Output must not contain or replace an input")
    marker = target / marker_name
    if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not marker.is_file())):
        raise DataQualityError("Output must be empty or a dedicated prior SEIFA output directory")


def _publish_directory(stage: Path, target: Path) -> None:
    backup = target.with_name(target.name + ".previous")
    if backup.exists():
        raise DataQualityError(f"Recovery folder exists: {backup}; inspect it before rerunning")
    if target.exists():
        target.rename(backup)
    try:
        stage.rename(target)
    except Exception:
        if backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def run_pipeline(
    workbook_path: str | Path,
    output_root: str | Path,
    consumer_csv: str | Path | None = None,
    consumer_postcode_column: str = "postcode",
    expected_poa_count: int | None = 2643,
) -> dict:
    workbook_path = Path(workbook_path).resolve()
    output_root = Path(output_root).resolve()
    consumer_path = Path(consumer_csv).resolve() if consumer_csv else None
    inputs = [workbook_path] + ([consumer_path] if consumer_path else [])
    _validate_output(output_root, inputs)
    workbook_hash = sha256(workbook_path)
    consumer_hash = sha256(consumer_path) if consumer_path else None

    features, reports = build_features(workbook_path, expected_poa_count=expected_poa_count)
    reports["data_dictionary"] = data_dictionary(features)
    join_status = "not_run_no_consumer_input"
    if consumer_path:
        coverage, missing, exceptions, counts = consumer_coverage(consumer_path, features, consumer_postcode_column)
        reports.update(
            consumer_join_coverage=coverage,
            consumer_feature_missingness=missing,
            consumer_postcode_exceptions=exceptions,
            consumer_postcode_counts=counts,
        )
        join_status = "completed_consumer_table_only"

    if sha256(workbook_path) != workbook_hash or (consumer_path and sha256(consumer_path) != consumer_hash):
        raise DataQualityError("An input changed during processing")

    metadata = {
        "stage": "external_seifa",
        "schema_version": 1,
        "source_year": 2021,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_filename": workbook_path.name,
        "source_url": SOURCE_URL,
        "methodology_url": METHODOLOGY_URL,
        "source_sha256": workbook_hash,
        "clean_rows": len(features),
        "clean_columns": len(features.columns),
        "all_four_scores_rows": int(features["seifa_all_scores_available"].sum()),
        "partial_score_rows": int((features["seifa_any_score_available"] & ~features["seifa_all_scores_available"]).sum()),
        "no_score_rows": int((~features["seifa_any_score_available"]).sum()),
        "consumer_join_status": join_status,
        "transaction_join_status": "not_run",
        "consumer_source_sha256": consumer_hash,
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "policies": {
            "special_geographies": "exclude 9494/9797 from join dimension; audit separately",
            "missing_index_scores": "retain ordinary POA; no imputation; distinguish from join failure",
            "quantiles": "retain official national/state ranks, deciles and percentiles",
            "scaled_features": "national percentile divided by 100 only; no fitted standardisation",
            "outliers": "retain all official scores; no trimming or winsorisation",
            "join": "normalised postcode, many-to-one LEFT JOIN, no row loss",
        },
        "limitations": [
            "SEIFA describes areas, not individual consumers or merchants",
            "POA is a Census approximation to postal delivery areas",
            "ABS recommends rankings or quantiles for most analysis rather than raw scores",
            "Do not interpret score ratios: a score of 1000 is not twice a score of 500",
            "State ranks are unavailable for Cross Border and Other Territories rows",
            "SEIFA 2021 is cross-sectional and should not be treated as a time trend",
            "Model weights must be estimated and validated later; this stage creates features only",
        ],
    }

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".seifa-", dir=output_root.parent))
    try:
        features.to_csv(stage / "seifa_clean.csv", index=False)
        features.to_parquet(stage / "seifa_clean.parquet", index=False, engine="pyarrow", compression="zstd")
        for name, report in reports.items():
            report.to_csv(stage / f"{name}.csv", index=False)
        (stage / "seifa_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        _publish_directory(stage, output_root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return metadata
