# Member 2 completed run

## Open the results

- `curation_summary/curation_summary.html`: readable summary without running Python.
- `curation_summary/curation_summary.ipynb`: notebook with saved actual outputs.
- `data/curated/curated_transactions/`: complete transaction dataset, partitioned by year and month. Read the entire directory, not just one monthly file.
- `data/curated/curation_audit.csv`: counts at each cleaning stage.
- `data/curated/join_coverage.csv`: internal join coverage.
- `data/curated/data_quality_profile.csv`: quality findings and pending integration.

## Verified results

14,195,505 input and output transactions; 14,195,505 unique order IDs; zero quarantined rows. Dates range from 2021-02-28 to 2022-10-26. Consumer joins match 100%; 580,830 transactions have no merchant master match and are retained with a flag. Output contains 21 monthly Parquet files. All four existing tests pass.

This is a copy of the supplied module with new run outputs. Cleaning source code is unchanged. Original input files were read without edits. Census and fraud enrichment are not part of this run.

## Reproduce

As described in README, put the base tables and all three transaction snapshot directories below one input directory, then run from this module directory:

```bash
python -m src.run_pipeline --data-root /absolute/path/to/tables --output-root data/curated
python -m pytest -q
```

Then run the summary notebook from `curation_summary/`.

The actual execution environment is recorded in `RUN_VERIFICATION.json`. Available local package versions were used; the pinned requirements file was not changed. The summary cells were run sequentially with IPython in the same process because this sandbox does not permit Jupyter kernel sockets.

The existing `.gitignore` deliberately excludes the large `data/curated/` outputs. Share the data separately from the code repository when needed.
