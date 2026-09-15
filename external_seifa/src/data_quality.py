"""Small, auditable quality summaries for the cleaned SEIFA dimension."""
from __future__ import annotations

import numpy as np
import pandas as pd


def profile_features(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for name in frame.columns:
        series = frame[name]
        records.append(
            {
                "field": name,
                "rows": len(series),
                "missing_rows": int(series.isna().sum()),
                "missing_rate": float(series.isna().mean()) if len(series) else None,
                "distinct_nonmissing": int(series.nunique(dropna=True)),
            }
        )
    return pd.DataFrame(records)


def score_distribution(frame: pd.DataFrame, score_columns: list[str]) -> pd.DataFrame:
    rows = []
    for field in score_columns:
        values = pd.to_numeric(frame[field], errors="coerce")
        rows.append(
            {
                "field": field,
                "available_rows": int(values.notna().sum()),
                "missing_rows": int(values.isna().sum()),
                "minimum": float(values.min()) if values.notna().any() else None,
                "p25": float(values.quantile(0.25)) if values.notna().any() else None,
                "median": float(values.median()) if values.notna().any() else None,
                "mean": float(values.mean()) if values.notna().any() else None,
                "p75": float(values.quantile(0.75)) if values.notna().any() else None,
                "maximum": float(values.max()) if values.notna().any() else None,
            }
        )
    return pd.DataFrame(rows)


def score_correlations(frame: pd.DataFrame, score_columns: list[str]) -> pd.DataFrame:
    matrix = frame[score_columns].corr(method="pearson", min_periods=2)
    matrix.index.name = "field"
    return matrix.reset_index()


def range_issues(frame: pd.DataFrame) -> pd.DataFrame:
    """Check final score/rank/quantile ranges without silently changing values."""
    problems: list[dict] = []
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        invalid = pd.Series(False, index=frame.index)
        rule = None
        if column.endswith("_national_decile") or column.endswith("_state_decile"):
            invalid = values.notna() & (~values.between(1, 10) | values.mod(1).ne(0))
            rule = "integer from 1 to 10"
        elif column.endswith("_national_percentile") or column.endswith("_state_percentile"):
            invalid = values.notna() & (~values.between(1, 100) | values.mod(1).ne(0))
            rule = "integer from 1 to 100"
        elif column.endswith("_national_rank") or column.endswith("_state_rank"):
            invalid = values.notna() & ((values < 1) | values.mod(1).ne(0))
            rule = "positive integer"
        elif column.endswith("_population_without_sa1_score_share"):
            invalid = values.notna() & ~values.between(0, 1)
            rule = "proportion from 0 to 1"
        elif column.endswith("_score") or column.endswith("_minimum_sa1_score") or column.endswith("_maximum_sa1_score"):
            invalid = values.notna() & (~np.isfinite(values) | (values <= 0))
            rule = "positive finite value"
        if rule:
            for idx in frame.index[invalid]:
                problems.append(
                    {
                        "postcode": frame.at[idx, "postcode"],
                        "field": column,
                        "value": frame.at[idx, column],
                        "expected": rule,
                    }
                )
    return pd.DataFrame(problems, columns=["postcode", "field", "value", "expected"])
