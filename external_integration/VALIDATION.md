# 外部数据接入验证报告

验证日期：2026-09-20（正文口径），补充统计更新于本次复核。以下数字基于当前代码与三个来源实际
清洗结果重新运行得到，并与仓库中 `results/` 的现有输出逐字段比对一致（可复现）。

## 邮编维度表

- 三个来源输入行数：census 2,641、seifa 2,641、ato 2,630（各自邮编唯一，参考年份单一）。
- outer join 后维度表 2,710 行、141 列（含 3 个来源匹配标记 + 1 个综合匹配标记 + 1 个邮编列）。
- 邮编在三源中的匹配组合（`postcode_source_patterns.csv`）：
  - census+seifa+ato 都匹配：2,561 个邮编
  - 只有 census+seifa（ato 未匹配）：80 个邮编
  - 只有 ato（census+seifa 未匹配）：69 个邮编
  - 2,561 + 80 + 69 = 2,710，无遗漏也无重复。

## 消费者层接入

- 输入 499,999 条消费者，接入后仍为 499,999 条（LEFT JOIN 不丢行，行数/唯一 ID 数校验通过）。
- 匹配率（`consumer_coverage.csv`）：
  - census_matched / seifa_matched：83.36%（416,818 / 499,999）
  - ato_matched：82.97%（414,825 / 499,999），与 `external_ato/VALIDATION.md` 中的数字一致
  - all_sources_matched：80.84%（404,185 / 499,999），与 ATO 报告中"联合邮编覆盖 80.84%"一致
- 逐字段缺失率（`consumer_feature_missingness.csv`）：141 个字段中 136 个存在缺失。

  **（本次复核修正）** 之前这里写"缺失比例与各来源的匹配率互补"，把所有缺失都归因于"邮编未匹配"，
  这不准确。`consumer_feature_missingness.csv` 本身就同时记录了 `missing_rows`（该字段总缺失行数）
  和 `matched_but_missing_rows`（**邮编已经匹配上、但该字段本身仍是空值**的行数）两栏，两者是不同
  的原因：
  - `missing_rows - matched_but_missing_rows` 部分，是邮编本身没有匹配到对应来源，缺失确实与匹配率
    互补，这部分推论成立。
  - `matched_but_missing_rows` 部分，是源数据自身在该字段上有缺失（例如某些 ABS/SEIFA 派生指标在
    小样本邮编上被抑制或未定义），跟"是否匹配"无关，即使邮编匹配上了，字段仍可能是空的。

  实测：136 个有缺失的字段里，**64 个字段存在 `matched_but_missing_rows > 0`**，也就是"匹配后来源
  字段/派生值本身缺失"，例如：

  | 字段 | 已匹配但字段仍缺失的消费者数 |
  |---|---:|
  | `census_avg_household_size` | 618 |
  | `seifa_irsd_national_decile` | 2,693 |
  | `seifa_irsd_state_decile` | 5,555 |

  这不一定是清洗环节的错误（源数据本身可能就有这些空值），但说明"缺失比例与匹配率互补"这个笼统
  说法不准确，需要分开看待。**不对这些值做补零或插补**——保持真实缺失是正确做法，只是文字描述要
  准确区分两种缺失原因。
- 未完全匹配邮编清单（`consumer_postcode_exceptions.csv`）：例如邮编 `0200`（三源均未匹配，145 条消费者
  记录）、`0801`/`0804`/`0811`（只匹配 ato，census/seifa 未覆盖）等，均有据可查。

## 交易层接入

`curated_transactions` 通过重新运行 member2 已提交到 GitHub 的清洗代码（`member2_curation/src/`），
对着 Canvas 发布的原始交易快照数据（`project-2-bnpl-tables-part2/3/4.zip`）本地跑出来，具体见
`member2_curation` 目录下的复现记录；跑出来的 `curation_metadata.json` 数字（input_rows 14,195,505、
quarantined_rows 0、unmatched_merchant_rows 580,830、amount_p99 1619.2727559488073）与 member2 原始
运行结果逐位一致。

在此基础上做交易层外部数据接入：

- 输入 14,195,505 条交易，接入后仍为 14,195,505 条（行数、唯一 order_id 数、原始列内容（哈希校验）、
  金额总和四项校验全部通过，连接前后完全一致）。
- 匹配率与金额覆盖率（`transaction_coverage.csv`）：
  | 指标 | 按笔数 | 按金额 |
  |---|---|---|
  | census_matched / seifa_matched | 83.51%（11,855,228 / 14,195,505） | 83.52% |
  | ato_matched | 82.84%（11,760,045 / 14,195,505） | 82.84% |
  | all_sources_matched | 80.77%（11,466,314 / 14,195,505） | 80.77% |

  **（本次复核修正）** 之前的版本在这里写"按笔数和按金额的覆盖率几乎相等，说明未匹配的交易在
  金额分布上没有系统性偏差"——这个推论不成立：两个覆盖率接近，最多说明匹配组和未匹配组的
  **平均**交易金额接近，不能证明金额分布、州分布或行业分布没有系统性偏差。改为下面这句，并补充
  实测数据：

  > 整体金额覆盖率与笔数覆盖率接近，但这本身不能排除分组内部（按州、按商户行业等）的覆盖率差异，
  > 需要单独查看分组统计。

  实测补充（本次复核新增，未匹配 vs 匹配两组）：

  | 分组 | n | p25 | p50（中位数） | p75 | p99 | 均值 |
  |---|---:|---:|---:|---:|---:|---:|
  | 未匹配（all_sources_matched=false） | 2,729,191 | 26.17 | 62.20 | 150.43 | 1614.79 | 166.28 |
  | 已匹配（all_sources_matched=true） | 11,466,314 | 26.12 | 62.24 | 150.46 | 1620.51 | 166.22 |

  按金额分布看，匹配组和未匹配组确实非常接近（分位数逐档只差几分钱到几元），**这一条具体结论是
  站得住的**。但按州拆开看，覆盖率差异很大，不是均匀的：

  | 州 | 交易数 | all_sources_matched 覆盖率 |
  |---|---:|---:|
  | SA | 1,612,955 | 94.1% |
  | QLD | 2,100,381 | 92.6% |
  | TAS | 525,947 | 92.6% |
  | VIC | 3,280,823 | 90.9% |
  | ACT | 130,325 | 82.8% |
  | NT | 202,178 | 69.1% |
  | WA | 2,247,663 | 68.3% |
  | NSW | 4,095,233 | 67.2% |

  NSW、WA、NT 三州的外部数据覆盖率明显低于 VIC/QLD/SA/TAS（相差 20+ 个百分点），这是因为这几州有
  更高比例的邮编没有同时出现在 census/seifa/ato 三个源里，不是随机缺失。**任何按州或按商户所在州
  切片的下游分析（含第 4 步行业增长图、后续排名模型）都应该知道这一点**：外部特征对 NSW/WA/NT 商户
  客群的覆盖天然更差，不能把"该商户没有地区特征"和"该商户地区特征不重要"混为一谈。行业维度的覆盖率
  拆分见 `merchant_features/VALIDATION.md`（现在按 census/seifa/ato 三个来源分别报告覆盖率）。

- 交易层用的是精简维度表（邮编 + 4 个匹配标记位），不含 141 个具体外部特征值：把 141 列贴到 1400 万行
  上计算和存储成本都很高，而且这一步真正要的是匹配率和行数一致性，不是每笔交易自己的地区特征。

  **（本次复核修正措辞）** 之前这里写"具体特征值留到商户级聚合之后再关联"，这个说法把顺序说反了。
  实际做法（`merchant_features/build_merchant_features.py`）是：**先在交易层按 consumer_postcode
  关联外部特征**（这样才能算出交易层面的匹配标记和覆盖率），**再按 merchant_abn 把已关联特征值的
  交易聚合成商户级别的交易量加权平均**——不是先把交易聚合成商户、再拿商户去关联邮编。本模块（
  `external_integration`）只做交易层"先关联、只留标记位"这一步验证匹配率和行数一致性；具体的
  141 个特征值要等到聚合到商户级别（几千行，而不是一千四百万行）时才真正贴上去，那一步也仍然是
  "交易先关联、再聚合"的顺序，只是把关联和聚合合并在同一次查询里做了。
- 未完全匹配邮编清单：`transaction_postcode_exceptions.csv`。
- `transaction_feature_missingness.csv` 为空：因为精简维度表里除了 4 个匹配标记位（`_matched` 结尾，
  按定义不计入逐字段缺失率统计）之外没有别的字段可统计缺失，这是设计上的预期结果，不是遗漏。

## 数据完整性校验（脚本内置，非事后抽查）

- 连接前后原始列的内容一致性校验：对消费者层（约50万行）用逐格双向比对（EXCEPT ALL）；对交易层
  （约1400万行）改用对全部原始列做聚合哈希比对（`sum(hash(...))`），两者验证的是同一件事——连接
  没有改动原始数据——但哈希校验在千万级数据量上开销小得多，避免了一次性对 14M 行做集合级双向比对
  导致的内存不足。
- 连接前后行数、唯一 ID 数完全一致，确认是 LEFT JOIN 而不是意外的多对多展开。
- 交易层额外校验金额总和连接前后一致（consumer 层无金额字段，此项不适用）。
- 任一校验失败会直接抛错终止运行，而不是只记录警告。

## 复核（第二轮）：两张诊断表之前没有生成代码

**发现：** `transaction_coverage_by_state.csv` 和 `transaction_amount_by_match_status.csv`
（`member3_summary.ipynb` 会读取）之前是用一次性的 device_bash 脚本跑出来的，数字经组员独立
核算是对的，但仓库里的 `integrate.py` 和 Notebook 代码里只有读取这两张表的地方，没有生成它们
的代码——组员从原始数据重跑整条流水线时生成不出来。

**修改：** 把生成逻辑做成 `integrate.py` 里的一个正式函数 `write_transaction_diagnostics(con,
out, joined_view='joined')`，在 `run()` 里紧跟着交易层的 `enrich()` 调用被执行，直接复用
`enrich()` 留下的 `joined` 临时视图（已经有 `consumer_state`、`dollar_value`、
`all_sources_matched`），不再是脱离流水线的一次性查询。重新跑一遍完整流水线
（`python integrate.py --consumers ../tables/tbl_consumer.csv --transactions
../member2_curation/data/curated/curated_transactions`），生成的两张表数值与复核之前完全一致
（按州覆盖率、按匹配状态的金额分位数逐位相同），证明这只是把生成过程接回了流水线，没有改变
任何数字。

## 自动化测试

运行：`python -m unittest discover -s external_integration/tests -v`
实际结果：7 项通过，无跳过（新增 2 项，覆盖 `write_transaction_diagnostics`：有
`consumer_state` 时按州覆盖率和按匹配状态金额分布都写出且数值正确；没有 `consumer_state`
时（例如只做了消费者层接入）安全跳过、不写出文件）。
覆盖范围：三源 outer join 与字段前缀、重复/多年份/空邮编被拒绝、交易层连接的行数/金额/缺失率统计、
分区 parquet 输入读取、重复 order_id 被拒绝、两张诊断表的生成与跳过逻辑。

## 使用范围

- 三个来源年份不同（Census/SEIFA 2021、ATO 2021-22），合并后仅作回顾性地区背景特征，不代表同一时点采集。
- `all_sources_matched` 只说明邮编键同时存在于三个来源，不代表该邮编下所有字段都完整（仍需看
  `consumer_feature_missingness.csv`）。
- **外部数据覆盖率按州分布不均**（NSW/WA/NT 明显低于其他州，见上表），使用者不应假设覆盖缺失是
  随机的。
- 本模块不做商户级特征构造、欺诈分析或商户评分，这些不属于接入阶段范围。
