# Results notebook

Open `census_summary.ipynb`. Saved outputs come from the actual Census ZIP and
consumer-table coverage audit, not invented fixtures. Use Run All after rerunning
the pipeline. The notebook locates `external_census/results/` from the repository,
package or notebook folder; set `CENSUS_RESULTS` for a different output directory.

The cleaning implementation lives in `src/`, not in notebook cells. The notebook
reports selection, excluded special geographies, numeric policies, safe ratios,
consumer coverage, post-join missingness, outlier distributions and limitations.

Full transaction enrichment is optional and has its own separate reports. Do not
present consumer-level coverage as transaction-level or merchant-level coverage.
