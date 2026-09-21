# 外部数据接入（Census + SEIFA + ATO）

把三个组员各自清洗好的邮编级别外部数据（Census、SEIFA、ATO）按邮编（postcode）合并成一张地区特征维度表，
再用它去关联消费者和交易数据。目的是给后续商户特征构造提供地区人口/收入/社会经济背景，不做商户评分或欺诈分析。

## 先看这些文件

- `VALIDATION.md`：实际运行的匹配率、覆盖情况和当前限制。
- `results/external_postcode_features.parquet` / `.csv`：三源合并后的邮编维度表（推荐用 parquet）。
- `results/data_dictionary.csv`：141 个字段各自的来源、定义、单位。
- `results/postcode_source_patterns.csv`：邮编在三个来源中的匹配组合分布。
- `results/consumer_coverage.csv`、`consumer_feature_missingness.csv`、`consumer_postcode_exceptions.csv`：
  消费者层面接入后的匹配率、逐字段缺失率、未完全匹配的邮编清单。
- `results/transaction_coverage.csv`、`transaction_feature_missingness.csv`、`transaction_postcode_exceptions.csv`：
  交易层面接入后的匹配率（按行数和按金额两种口径）、未完全匹配的邮编清单。
- `results/transaction_coverage_by_state.csv`、`transaction_amount_by_match_status.csv`：
  按消费者所在州拆分的匹配率、按匹配状态拆分的交易金额分布（复核发现单一整体覆盖率会掩盖州与州
  之间的差异）。由 `integrate.py` 里的 `write_transaction_diagnostics()` 在 `run()` 中自动生成，
  不是脱离流水线的一次性脚本，可复现；详见 VALIDATION.md。
- `results/metadata.json`：三个来源文件的路径、SHA-256、行数，以及本次运行的版本信息。

## 运行

从小组仓库根目录运行：

```bash
python -m pip install -r external_integration/requirements.txt
python external_integration/integrate.py \
  --consumers ../project-2-bnpl-tables-part1.zip \
  --transactions member2_curation/data/curated/curated_transactions \
  --output external_integration/results_rerun_new
python -m unittest discover -s external_integration/tests -v
```

输出目录必须为空；上面重跑写到新目录，不要覆盖 `results/`。`--transactions` 指向 member2 输出的
`curated_transactions`（parquet 分区文件夹）；不传就只做消费者层接入。

## 接入逻辑

1. `build_dimension`：分别读取 `external_census/results/census_clean.parquet`、
   `external_seifa/results/seifa_clean.parquet`、`external_ato/results/ato_clean.parquet`，
   校验邮编唯一且非空、参考年份单一（Census/SEIFA 为 2021，ATO 为 2021-22），
   字段名加来源前缀后按邮编做 outer join，保留每个来源的匹配标记（`{source}_matched`）和综合标记
   （`all_sources_matched`）。
2. `enrich`：把维度表 LEFT JOIN 到消费者或交易数据上（按标准化后的四位邮编）。
   连接前后做了三重校验：行数与唯一 ID 数不变、原始列的值完全不变（哈希校验，等价于逐格比对但对千万级
   数据量更省内存）、金额总和（仅交易层）不变；任何一项校验失败都会直接报错终止，而不是只写一份报告。
3. 每次接入都会输出：匹配率报告（`{scope}_coverage.csv`）、逐字段缺失率（`{scope}_feature_missingness.csv`）、
   未完全匹配的邮编清单（`{scope}_postcode_exceptions.csv`）。
4. **消费者层**和**交易层**用的维度表粒度不同：消费者层（约50万行）用完整的 141 列外部特征表；
   交易层（约1400万行）默认只用精简维度表（`reduce_dimension`：邮编 + 4 个匹配标记位，不含 141 个具体
   特征值）——把全部 141 列贴到 1400 万行交易上计算量和存储量都很大，而 overview 里也明确说了不需要
   把所有数据存成一张巨大的交易表。交易层这一步要拿到的是匹配率和连接前后行数是否一致，不是每笔交易
   的地区特征值本身；具体特征值留到第三步按商户聚合之后再关联（那时候是几千行，不是一千四百万行，
   关联全部 141 列成本很低）。需要交易层也带完整 141 列时可以加 `--transactions-full`，但会明显更慢、
   输出也大得多。

## 当前进度

- 邮编维度表：已完成，2,710 个邮编、141 列。
- 消费者层接入：已完成，499,999 条消费者全部保留（LEFT JOIN 不丢行），三源联合匹配率 80.84%。
- 交易层接入：**已完成**，14,195,505 条交易全部保留，金额总和连接前后一致。三源联合匹配率（按笔数）
  80.77%，按金额口径 80.77%，与消费者层的 80.84% 非常接近。用的是精简维度表（见上）。
- `curated_transactions`（member2 的交易数据本体，1400多万行）此前一直没有到手：GitHub 和本地都只有
  审计/统计 CSV，没有数据本体。后来发现原始交易快照数据是这门课统一发给全组/全班的（Canvas 上
  "Dataset Release"），不是 member2 独有的东西；已经用你从 Canvas 下载的 `project-2-bnpl-tables-part2/3/4.zip`
  （原始交易快照）+ member2 已提交到 GitHub 的清洗代码（`member2_curation/src/`），在本地重新跑出了
  `curated_transactions`，跑出来的行数、去重结果、商户匹配数、p99 金额都和 member2 原始跑出来的
  `curation_metadata.json` 完全一致（可复现）。这份数据本体很大（790MB），已经在 `.gitignore` 里排除，
  不会被提交到 GitHub。

## 来源和时间边界

- Census、SEIFA 数据沿用组员已清洗的版本，本模块不重新清洗，只做字段重命名、来源标记和邮编对齐。
- ATO 数据引用 `external_ato/results/ato_clean.parquet`，口径见该模块自己的 README/VALIDATION。
- 三个来源分别是 2021 Census、2021 SEIFA、2021-22 ATO 收入年度，口径和采集时点不完全相同，
  仅作回顾性地区背景使用，不代表交易发生时点已知的信息。
- ATO 邮编与 ABS POA 沿用邮编（POA）方案，未做 SA2 转换。
