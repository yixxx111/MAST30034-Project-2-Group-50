# Curation summary

Open `curation_summary.ipynb` for code and saved actual outputs, or `curation_summary.html` for a presentation view.

The summary covers inputs and join keys, cleaning rules, row-count audit, join coverage, actual timeline, amount distribution and outlier impact, merchant non-match diagnostics, field-level missingness and downstream limitations.

The original `src/` cleaning logic and tests are unchanged. This update adds `build_analysis.py` and five aggregate CSV reports under `analysis_results/`. All seven notebook code cells executed successfully, and all four existing tests passed again. Notebook execution used an in-process IPython session; saved plots and tables derive from the actual supplied data.

To rebuild aggregates after a new pipeline run, run from the module root:

```bash
python curation_summary/build_analysis.py --merchant-master /path/to/tbl_merchants.parquet
```

Then run the notebook from this folder. Displaying/rerunning the notebook requires the CSV reports in `../data/curated/` and `analysis_results/`, but not the full transaction Parquet. Regenerating the analysis reports does require the full Parquet dataset. Without the optional merchant-master argument, the ABN diagnostic is omitted.

Core results: 14,195,505 unique orders, zero quarantined rows, 100% consumer join coverage and 95.91% merchant row coverage. Unmatched merchants account for 8.59% of recorded transaction value. The global p99 flags 141,956 transactions accounting for 20.94% of value; these are retained, not classified as fraud.

This GitHub-ready copy includes the code, small CSV/JSON reports, executed notebook and HTML. It omits the full transaction and quarantine Parquet outputs. Obtain row-level data separately when needed; this folder is not a full data archive.
