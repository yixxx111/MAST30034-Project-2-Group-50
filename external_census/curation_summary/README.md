# Results notebook

Open `census_summary.ipynb`. Use Run All after rerunning the pipeline. The notebook
uses relative paths to `../results/`; it intentionally contains no hard-coded
machine path or environment-variable configuration.

The cleaning implementation lives in `src/`, not in notebook cells. The notebook
reports selection, excluded special geographies, numeric policies, safe ratios,
the published-versus-derived rate check, consumer coverage when supplied,
post-join missingness, outlier distributions and limitations.

Full transaction enrichment is optional and has its own separate reports. Do not
present consumer-level coverage as transaction-level or merchant-level coverage.
