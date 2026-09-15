"""Create and execute a concise notebook from saved SEIFA pipeline outputs."""
from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor


def build_notebook(results: Path, output_dir: Path, kernel_name: str = "ads-project2-member1") -> Path:
    results = results.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    notebook = nbf.v4.new_notebook()
    notebook["metadata"]["kernelspec"] = {
        "display_name": "ADS Project 2 Member 1",
        "language": "python",
        "name": kernel_name,
    }
    notebook["metadata"]["language_info"] = {"name": "python", "version": "3.12"}
    notebook["cells"] = [
        nbf.v4.new_markdown_cell(
            """# ABS SEIFA 2021 Postal Area curation

This notebook summarises the saved outputs from the reproducible SEIFA pipeline. The source is the Australian Bureau of Statistics **Postal Area, Indexes, SEIFA 2021** workbook. SEIFA describes areas rather than individual people or merchants.

Four indexes are retained: IRSD, IRSAD, IER and IEO. For most analysis, ABS recommends ranks or quantiles rather than treating raw scores as quantities."""
        ),
        nbf.v4.new_code_cell(
            f"""from pathlib import Path
import json
import pandas as pd
import matplotlib.pyplot as plt

RESULTS = Path(r{str(results)!r})
clean = pd.read_parquet(RESULTS / 'seifa_clean.parquet')
coverage = pd.read_csv(RESULTS / 'consumer_join_coverage.csv')
distribution = pd.read_csv(RESULTS / 'score_distribution.csv')
quality = pd.read_csv(RESULTS / 'feature_quality_profile.csv')
metadata = json.loads((RESULTS / 'seifa_metadata.json').read_text(encoding='utf-8'))

print(f"Clean dimension: {{len(clean):,}} POAs x {{len(clean.columns)}} columns")
print(f"All four scores: {{metadata['all_four_scores_rows']:,}}")
print(f"Partial scores: {{metadata['partial_score_rows']:,}}")
print(f"No scores: {{metadata['no_score_rows']:,}}")"""
        ),
        nbf.v4.new_markdown_cell("## Consumer postcode coverage"),
        nbf.v4.new_code_cell(
            """consumer_coverage = coverage.query("scope == 'consumer_rows'").copy()
consumer_coverage[['metric', 'rows', 'denominator', 'rate']]"""
        ),
        nbf.v4.new_code_cell(
            """plot_data = consumer_coverage[consumer_coverage['rows'] > 0].sort_values('rows')
ax = plot_data.plot.barh(x='metric', y='rows', legend=False, color='#4472C4', figsize=(8, 4.5))
ax.set_title('Consumer rows by SEIFA match status')
ax.set_xlabel('Consumer rows')
ax.set_ylabel('')
ax.xaxis.set_major_formatter(lambda x, pos: f'{x:,.0f}')
plt.tight_layout()
plt.show()"""
        ),
        nbf.v4.new_markdown_cell("## National percentile distributions"),
        nbf.v4.new_code_cell(
            """percentile_columns = {
    'IRSD': 'seifa_irsd_national_percentile',
    'IRSAD': 'seifa_irsad_national_percentile',
    'IER': 'seifa_ier_national_percentile',
    'IEO': 'seifa_ieo_national_percentile',
}
fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True, sharey=True)
for ax, (label, column) in zip(axes.ravel(), percentile_columns.items()):
    clean[column].dropna().plot.hist(bins=10, range=(0.5, 100.5), ax=ax, color='#70AD47', edgecolor='white')
    ax.set_title(label)
    ax.set_xlabel('National percentile (higher = higher index value)')
    ax.set_ylabel('POAs')
fig.suptitle('SEIFA 2021 national percentile distributions by POA', y=1.01)
plt.tight_layout()
plt.show()"""
        ),
        nbf.v4.new_markdown_cell("## Score availability and official caution flags"),
        nbf.v4.new_code_cell(
            """summary = pd.Series({
    'ordinary_POAs': len(clean),
    'all_four_scores': int(clean['seifa_all_scores_available'].sum()),
    'partial_scores': int((clean['seifa_any_score_available'] & ~clean['seifa_all_scores_available']).sum()),
    'no_scores': int((~clean['seifa_any_score_available']).sum()),
    'ABS_caution_flag': int(clean['seifa_caution_low_sa1_representation'].fillna(False).sum()),
    'cross_state_boundary': int(clean['seifa_cross_state_boundary'].fillna(False).sum()),
}, name='rows')
summary.to_frame()"""
        ),
        nbf.v4.new_markdown_cell(
            """## Interpretation and use

- Join the dimension to consumers or curated transactions using a normalised four-character postcode.
- Keep unmatched rows and use the explicit match-status field; do not replace a missing score with zero.
- Low scores, deciles and percentiles indicate relatively greater disadvantage. They do **not** measure an individual's income, education or creditworthiness.
- The four indexes overlap conceptually. Do not average all four into a merchant score without a documented model and validation, because that can double-count related information.
- SEIFA 2021 is a cross-sectional area measure. It is not a 2020–2021 economic trend and should not be used as though it were time-series GDP."""
        ),
    ]

    notebook_path = output_dir / "seifa_summary.ipynb"
    nbf.write(notebook, notebook_path)
    executor = ExecutePreprocessor(timeout=180, kernel_name=kernel_name)
    executor.preprocess(notebook, {"metadata": {"path": str(output_dir)}})
    nbf.write(notebook, notebook_path)
    html, _ = HTMLExporter(template_name="lab", exclude_input=True).from_notebook_node(notebook)
    (output_dir / "seifa_summary.html").write_text(html, encoding="utf-8")
    return notebook_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the executed SEIFA summary notebook and HTML.")
    parser.add_argument("--results", type=Path, default=Path("external_seifa/results"))
    parser.add_argument("--output-dir", type=Path, default=Path("external_seifa/curation_summary"))
    parser.add_argument("--kernel", default="ads-project2-member1")
    args = parser.parse_args()
    print(build_notebook(args.results, args.output_dir, args.kernel))


if __name__ == "__main__":
    main()
