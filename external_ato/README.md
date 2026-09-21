# ATO 2021–22 邮编数据清洗

将官方 Table 6B 整理为每个邮编一行的地区税务特征。保留 Claude 修改后的 21 列指标结构，补齐 Parquet、验证报告和可执行 summary。
这是回顾性地区背景分析；不执行商户评分或欺诈分析。

## 先看这些文件

- `VALIDATION.md`：实际运行的质量结果及限制。
- `curation_summary/ato_summary.ipynb`：已执行的分析说明、检查和图表。
- `curation_summary/ato_summary.html`：可直接用浏览器阅读。
- `results/ato_clean.parquet`：推荐作为后续连接输入，邮编已保存为文本。
- `results/ato_clean.csv`、`results/data_dictionary.csv`：文本版数据及每列含义。
- `median_reference/`：独立保存的官方 Table 8 中位数/均值候选，未替换核心特征。

## VS Code 运行

从小组仓库根目录运行；不要在外层 project2 目录直接运行相对命令。

```bash
source external_ato/.venv/bin/activate
python -m pip install -r external_ato/requirements.txt
python external_ato/clean_ato.py --consumers ../project-2-bnpl-tables-part1.zip \
  --compare census=external_census/results/census_clean.csv seifa=external_seifa/results/seifa_clean.csv \
  --output external_ato/results_rerun_new
python -m unittest discover -s external_ato/tests -v
python external_ato/build_summary.py
```

清洗输出必须使用空目录；上面重跑写到新目录。summary 默认读取 `external_ato/results/`，不会自动替换为重跑结果。
首次使用可先 `python3 -m venv external_ato/.venv`。包依赖包含 notebook 和 Parquet 支持。
不传 --consumers 时只清洗 ATO；--compare 需要同时提供消费者输入。

```python
import pandas as pd
ato = pd.read_parquet("external_ato/results/ato_clean.parquet")
# 如必须读 CSV，只固定邮编类型：
ato_csv = pd.read_csv("external_ato/results/ato_clean.csv", dtype={"postcode": "string"})
```

## 清洗规则

1. 只使用 Table 6B，不叠加 6A 子组；6A 用来逐字段对账，差异超过金额容差 2 AUD（计数容差 0）或键缺失时停止输出。
2. 核对表头及年份，邮编转四位文本，低于 0200 作为项目有效范围检查。州 other/海外聚合记录独立审计；未知标签、州错误和重复邮编报错。
3. 数值缺失/非法计数保留 null 并记录；未知文本符号报错待核查。保留真实零值和负收入，不填补、不缩尾、不删除统计极端值。
4. 应税收入和工资派生均值各自采用对应标签人数。工资人数占比不是人口就业率，净税额人数占比不是风险指标。
5. 分母低于 100 只作项目审阅标记；SA4 州 other 只作来源占位标记，不从中断定地理缺失原因。
6. 保留现行版本对 total income 的字段选择，不将高度相关的收入指标当作独立证据；本清洗模块不决定最终特征权重。
7. 消费者覆盖同时报告州差异和三个来源的联合键覆盖；号段分类仅是启发式，不能证明未匹配原因。

## 官方中位数

已确认 Table 8 存在，2021–22 候选统计有 2,270 个可用邮编；表内另 47 行该年 na 保留为 null。
原表说明该年度只发布超过 200 份申报的邮编。Table 8 的官方平均值也单独保留，不假定等于 Table 6B 派生值。
选择中位数还是均值留到商户特征阶段；可以比较覆盖，但不能插补不存在的官方中位数。

```bash
python external_ato/inspect_median.py \
  --xlsx tables/external/ato_2021_22/ts22individual08medianaveragetaxableincomestatepostcode.xlsx \
  --output external_ato/median_reference_rerun
```

## 来源和时间边界

Australian Taxation Office, Taxation statistics 2021–22. CC BY 2.5 Australia（目录许可证）。清洗和派生计算为项目工作。
原始 Excel 保留在 `tables/external/ato_2021_22/`，不修改原件；两份来源元数据和哈希保存在模块中。
- Table 6 目录：https://data.gov.au/data/dataset/taxation-statistics-postcode-data
- Table 8：https://data.gov.au/data/dataset/4be150cc-8f84-46b8-8c61-55ff1d48a700/resource/9bd9d5af-2c09-405f-b484-69c862f4dc2e

2021–22 是收入年度，不是可获得日期。该版本使用截至 2023-10-31 处理的申报，不能当作交易发生时已知的预测特征。
ATO 邮编与 ABS POA 并非完全相同；ATO 年度个人收入与 Census 家庭周收入也非同一口径。
原始 Excel 通常被项目 tables 忽略规则排除；分享时需同时提供来源链接或原件。
