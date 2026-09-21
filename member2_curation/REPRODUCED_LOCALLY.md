# 本地复现说明（2026-09-20）

`data/curated/curated_transactions/`、`quarantined_transactions.parquet` 以及
`curation_audit.csv`、`join_coverage.csv`、`snapshot_coverage.csv`、`merchant_match_exceptions.csv`、
`data_quality_profile.csv`、`curation_metadata.json` 这几个文件，是在这台电脑上重新跑
`src/run_pipeline.py`（清洗代码完全未改动）生成的，不是原作者提供的文件本体。

## 为什么要重新跑

`curated_transactions`（1400多万行交易明细）体积太大，一直没有从原作者那里传过来，GitHub 上也只有
审计/统计类 CSV，没有数据本体。后来发现原始交易快照数据（`transactions_*_snapshot/`）是这门课统一
发给全组的（Canvas 上 "Dataset Release"），不是原作者独有的，所以可以直接下载官方原始数据，配合仓库
里已经提交的清洗代码，在本地独立跑出同一份 `curated_transactions`，不需要再等待或依赖原作者的电脑。

## 复现结果核对

跑出来的关键数字和原作者 `RUN_RESULTS.md` / `RUN_VERIFICATION.json` 里记录的完全一致：

| 指标 | 原作者记录 | 本地复现 |
|---|---|---|
| 输入/输出交易数 | 14,195,505 | 14,195,505 |
| 唯一 order_id 数 | 14,195,505 | 14,195,505 |
| 隔离（quarantined）行数 | 0 | 0 |
| 商户未匹配交易数 | 580,830 | 580,830 |
| 金额 p99 | 1619.2727559488073 | 1619.2727559488073 |
| 自动化测试 | 4 passed | 4 passed |

`join_coverage.csv` 逐字节一致；`merchant_match_exceptions.csv` 397 行完全一致（金额字段末位有浮点数
累加顺序导致的极小舍入差异，属正常现象，不影响数值本身）。

## 数据本体不进仓库

`curated_transactions/`（约790MB）和 `quarantined_transactions.parquet` 已经加进根目录 `.gitignore`，
不会被提交到 GitHub，跟原作者一直以来对这份数据的处理方式一致。
