# Summary update

This copy expands the submitted curation summary. The original cleaning source, tests and all original result-file contents are unchanged. Copy suffixes were removed from file and directory names in this copy so standard paths work.

New evidence: full-data amount distribution, p99 row/value shares, unmatched merchant row/value shares and ABN representation checks against the supplied merchant master. Five aggregate analysis files and their reproducible builder are included. The expanded English notebook has seven executed code cells, no cell errors, and an exported HTML presentation. All four existing tests passed again. Parquet/result checksums match the supplied submission copy.

This GitHub-ready folder contains code, the executed notebook, HTML and small CSV/JSON reports needed to display or rerun the summary. It does not contain the full transaction partitions or quarantine Parquet. Full row-level data are required to regenerate the aggregate analyses; the original input tables are required to rerun curation. Keep the established module folder name when adding it to the group repository.
