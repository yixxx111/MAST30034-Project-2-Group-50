"""Build and execute the ATO summary using the current Python environment."""
import json
import os
import sys
import tempfile
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager


def main():
    module = Path(__file__).resolve().parent
    output = module / 'curation_summary'
    output.mkdir(exist_ok=True)
    cells = []
    md = lambda s: cells.append(nbf.v4.new_markdown_cell(s))
    code = lambda s: cells.append(nbf.v4.new_code_cell(s))
    md('''# ATO 2021-22 postcode data cleaning results

This report reads and verifies the saved cleaning results live, showing the processing rules,
coverage, quality, and usage limits. Each row is a postcode. Regional tax statistics are not an
individual's purchasing power or credit risk.
Full transaction integration and merchant scoring were not done here.
''')
    code('''from pathlib import Path
import json
import pandas as pd
import pyarrow.parquet as pq
import matplotlib.pyplot as plt
from IPython.display import display

cwd = Path.cwd()
module = next(p for p in [cwd, cwd.parent, cwd / 'external_ato'] if (p / 'results/metadata.json').exists())
results = module / 'results'
meta = json.loads((results / 'metadata.json').read_text())
df = pd.read_parquet(results / 'ato_clean.parquet')
csv_df = pd.read_csv(results / 'ato_clean.csv', dtype={'postcode': 'string'})
pd.testing.assert_frame_equal(df, csv_df, check_dtype=False)
assert df.postcode.is_unique and df.postcode.str.fullmatch(r'[0-9]{4}').all()
assert '0800' in set(df.postcode)
display(pd.DataFrame([{'raw_rows':meta['source_data_rows'], 'clean_rows':len(df), 'columns':len(df.columns),
                       'excluded_rows':meta['excluded_rows'], 'quality_issues':meta['quality_issue_cells']}]))
display(df.head())
''')
    md('''## 1. Cleaning and reconciliation

Only the full individual records from Table 6B are used; Table 6A's two tax-status sub-groups are
used to reconcile against them, not added in again. Postcodes are normalised, state "other" and
Overseas aggregate records are isolated, and unique keys, values, ratios, and denominators are
checked. Genuine zero values and negative income are kept -- no imputation, winsorising, or removal
of statistical outliers. The exact cause of a state-"other" record can't be determined from the
label alone. The pipeline stops if a reconciliation key or value doesn't match.
''')
    code('''excluded = pd.read_csv(results / 'excluded_records.csv')
reconciliation = pd.read_csv(results / 'table6a_reconciliation.csv')
assert reconciliation[['records_only_in_6a','records_only_in_6b','records_beyond_tolerance']].to_numpy().sum() == 0
display(excluded)
display(reconciliation)
display(pd.read_csv(results / 'feature_quality_profile.csv'))
''')
    md('''## 2. Consumer postcode coverage

Coverage rates use the project's provided synthetic consumer records as the denominator -- this is
not Australia's population coverage, nor transaction/transaction-amount coverage. Combined coverage
only means the postcode exists in all three tables; it doesn't guarantee every Census/SEIFA metric
has a value. A state mismatch is only logged for audit, not treated as proof the consumer's record
is wrong. The number-range label is a heuristic and doesn't prove the reason for a non-match.
''')
    code('''coverage = pd.read_csv(results / 'consumer_join_coverage.csv')
display(coverage)
display(pd.read_csv(results / 'consumer_state_consistency.csv'))
display(pd.read_csv(results / 'consumer_feature_missingness.csv'))
base = coverage[(coverage.scope == 'consumer_rows') & coverage.metric.isin(['matched','postcode_not_in_ato'])]
fig, ax = plt.subplots(figsize=(7,3))
ax.barh(['Matched','Not in ATO'], [base.loc[base.metric.eq(k),'rows'].iloc[0] for k in ['matched','postcode_not_in_ato']], color=['#267b94','#bd7245'])
ax.set_xlabel('Consumer records'); ax.set_title('ATO postcode coverage')
fig.tight_layout()
(module / 'curation_summary/figures').mkdir(exist_ok=True)
fig.savefig(module / 'curation_summary/figures/consumer_coverage.png', dpi=150)
plt.show()
''')
    md('''## 3. Regional metrics and quality flags

Taxable-income amounts are divided by their corresponding reporter-count label; salary amounts are
divided by the salary-recipient count. These are means, not medians. Salary-recipient share is not
an employment rate, and net-tax-payer share is not a compliance or fraud indicator. A derived-metric
denominator below 100 is this project's own review threshold, not an official suppression rule, and
no postcode is removed for it.
''')
    code('''display(pd.read_csv(results / 'data_dictionary.csv'))
display(pd.DataFrame({'flag':['small denominator','SA4 state-other placeholder'],
                      'postcodes':[df.ato_small_denominator.sum(),df.ato_sa4_is_state_other.sum()]}))
fig, ax = plt.subplots(figsize=(7,3))
ax.hist(df.ato_taxable_income_or_loss_per_reporter, bins=40, color='#267b94')
ax.set_xlabel('Derived taxable income or loss per reporter (AUD/year)')
ax.set_ylabel('Postcodes'); ax.set_title('Area income distribution; extremes retained')
fig.tight_layout(); fig.savefig(module / 'curation_summary/figures/income_distribution.png', dpi=150)
plt.show()
''')
    md('''## 4. Official median candidate table

Separately verified against Individuals Table 8, keeping the official 2021-22 mean and median.
Its Notes state that only postcodes with more than 200 returns are published for that year;
unpublished values are kept as missing `na`. The official Notes define their own statistical basis
for the mean/median. The median can't be inferred from Table 6B's totals, and the two tables' means
shouldn't be assumed identical either. This candidate table is only kept here, not merged to replace
the existing features.
''')
    code('''median = pd.read_parquet(module / 'median_reference/ato_table8_candidates.parquet')
median_meta = json.loads((module / 'median_reference/metadata.json').read_text())
display(pd.DataFrame([{'postcodes_in_table8':len(median), 'available_2021_22':median.ato_table8_available.sum(),
                       'unavailable_2021_22':(~median.ato_table8_available).sum()}]))
display(median.head())
print(median_meta['source_url'])
''')
    md('''## 5. Usage boundaries and next steps

This is the 2021-22 income year, as processed up to 2023-10-31 -- it should not be treated as a
predictive input known at the time a transaction occurred. ATO postcodes are not identical to ABS
POAs. Missingness must not simply be zero-filled, and regional metrics must not be interpreted as
facts about an individual. Next, join by consumer postcode, check that row counts and merchant
coverage are unchanged, then decide whether to use the mean or the official median.
Full transaction integration, merchant-feature construction, fraud analysis, and ranking have not
been done yet.
''')
    code('''print('Source:', meta['source_url'])
print('Workbook SHA-256:', meta['source_sha256'])
print('Data processing cutoff:', meta['source_processing_cutoff'])
print('Parquet postcode type:', pq.read_schema(results / 'ato_clean.parquet').field('postcode').type)
''')
    nb = nbf.v4.new_notebook(cells=cells, metadata={'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'}})
    with tempfile.TemporaryDirectory(prefix='ato-kernel-') as temp:
        kernel = Path(temp) / 'ato-summary'
        kernel.mkdir()
        (kernel / 'kernel.json').write_text(json.dumps({'argv':[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],
                                                      'display_name':'ATO summary', 'language':'python'}))
        km = KernelManager(kernel_name='ato-summary', kernel_spec_manager=KernelSpecManager(kernel_dirs=[temp]))
        NotebookClient(nb, km=km, timeout=120, resources={'metadata':{'path':str(module)}}).execute()
    errors = [o for c in nb.cells if c.cell_type == 'code' for o in c.get('outputs',[]) if o.output_type == 'error']
    if errors:
        raise RuntimeError(errors)
    nbf.validate(nb)
    nbf.write(nb, output / 'ato_summary.ipynb')
    exporter = HTMLExporter()
    exporter.exclude_input = True
    html, _ = exporter.from_notebook_node(nb)
    (output / 'ato_summary.html').write_text(html, encoding='utf-8')
    print(f'Executed {sum(c.cell_type == "code" for c in nb.cells)} code cells, no errors; notebook and HTML saved.')


if __name__ == '__main__':
    main()
