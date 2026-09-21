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
    md('''# ATO 2021–22 邮编数据清洗结果

本报告从保存的清洗结果实时读取并验证数据，展示处理规则、覆盖率、质量与使用限制。
每行是一个邮编。地区税务统计不是个人购买力或信用风险。
此次没有合并全量交易，没有计算商户分数。
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
    md('''## 1. 清洗及对账

只使用 Table 6B 的全部个人记录；Table 6A 的两个税务状态用于对账，不重复加入。
规范邮编，隔离州 other 和 Overseas 汇总记录，检查唯一键、数值、比例及分母。
保留真实零值和负收入；不填补、不缩尾、不删除统计极端值。
州 other 的具体形成原因不能只凭标签确定。对账键或数值不符时流水线停止。
''')
    code('''excluded = pd.read_csv(results / 'excluded_records.csv')
reconciliation = pd.read_csv(results / 'table6a_reconciliation.csv')
assert reconciliation[['records_only_in_6a','records_only_in_6b','records_beyond_tolerance']].to_numpy().sum() == 0
display(excluded)
display(reconciliation)
display(pd.read_csv(results / 'feature_quality_profile.csv'))
''')
    md('''## 2. 消费者邮编覆盖

覆盖率以项目提供的合成消费者记录为分母，不是澳大利亚人口覆盖率，也不是交易/交易金额覆盖率。
联合覆盖仅表示邮编同时存在于三张表，不保证所有 Census/SEIFA 指标都有值。
州不一致只作审计，不能直接认定消费者填错。号段标签为启发式，不证明未匹配原因。
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
    md('''## 3. 地区指标与质量标记

应税收入金额除以对应标签人数；工资金额除以工资人数。它们不是中位数。
工资人数占比不是人口就业率，净税额人数占比不是合规或欺诈指标。
派生指标分母少于 100 是项目审阅阈值，不是官方抑制规则，也不删除邮编。
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
    md('''## 4. 官方中位数候选表

已另外核实 Individuals Table 8，并保留官方 2021–22 年均值和中位数。
其 Notes 说明该年度只发布申报量超过 200 的邮编；未发布的 `na` 保留缺失。
官方 Notes 对均值/中位数的统计口径有专门定义。不可从 Table 6B 总额推算中位数，
也不要默认不同表的平均值完全相同。这里仅保存候选，不替换现有特征。
''')
    code('''median = pd.read_parquet(module / 'median_reference/ato_table8_candidates.parquet')
median_meta = json.loads((module / 'median_reference/metadata.json').read_text())
display(pd.DataFrame([{'postcodes_in_table8':len(median), 'available_2021_22':median.ato_table8_available.sum(),
                       'unavailable_2021_22':(~median.ato_table8_available).sum()}]))
display(median.head())
print(median_meta['source_url'])
''')
    md('''## 5. 使用边界及下一步

这是 2021–22 收入年度、截至 2023-10-31 处理申报的版本，不能视为交易发生时已知的预测输入。
ATO 邮编与 ABS POA 并不完全相同。缺失不得直接填零，地区指标不得解释为个体事实。
后续按消费者邮编连接，检查行数不变和商户覆盖率，再决定采用均值或官方中位数。
尚未进行全量交易接入、商户特征构造、欺诈分析或排名。
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
