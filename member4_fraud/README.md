# Member 4 - Fraud risk analysis

This folder contains the Member 4 fraud-risk work only. It does not calculate the final business score, optimise the group ranking, or select the Top 100 merchants.

## Folder layout

- `code/`: the three executed Jupyter notebooks, their supporting Python modules, and `requirements.txt`.
- `result/`: CSV files retained for downstream ranking, reporting, and model validation.

Generated model binaries, figures, Parquet working tables, Python bytecode, and temporary DuckDB files are intentionally excluded because the notebooks can reproduce them.

## Run order

Run the notebooks from the repository, `member4_fraud`, or `member4_fraud/code` directory:

1. `code/consumer_fraud_analysis.ipynb`
2. `code/merchant_fraud_analysis.ipynb`
3. `code/fraud_risk_profile.ipynb`

Install the pinned packages with `python -m pip install -r code/requirements.txt`. The raw tables are expected at `../data/part1/tables` relative to the repository, and curated transactions at `member2_curation/data/curated/curated_transactions`.

The notebooks write large intermediate artefacts to the operating system's temporary directory and copy only selected CSV deliverables into `result/`.

## Main downstream outputs

- `consumer_fraud_predictions_all.csv`: consumer risk scores at the 2022-02-28 scoring date.
- `merchant_fraud_predictions_all.csv`: cross-validated KNN merchant fraud scores.
- `merchant_fraud_risk_profile.csv`: merchant-level KNN plus consumer-exposure risk used by downstream ranking work.
- `fraud_risk_coverage_summary.csv`: coverage and evidence availability checks.
- `knn_fraud_risk_scores.csv`: KNN score, KNN percentile, combined risk, and safety score.
- `consumer_model_comparison.csv` and `merchant_model_comparison.csv`: model selection evidence.

## Interpretation and limitations

The consumer target is a supplied probability score, not a confirmed fraud event. Consumer predictions are end-of-day risk estimates and cannot be attributed to an individual transaction when a consumer used multiple merchants that day. Only 61 merchants have direct merchant-fraud observations, so the merchant model is highly uncertain. The final `fraud_risk_index` is a relative review-prioritisation index, not a fraud probability or proof of wrongdoing.

The combined risk profile uses an amount-weighted consumer exposure percentile and a KNN merchant-risk percentile. To avoid double counting, KNN uses merchant behavioural features only; consumer-risk exposure features are excluded from KNN and introduced after prediction. The KNN pipeline applies median imputation and `StandardScaler` within each repeated cross-validation fold, then chooses the best neighbour count and weighting by cross-validated MAE. The two percentiles receive equal weight when both are available; the sole available source is used otherwise. Direct merchant observations are training labels and diagnostics only, rather than a second manually inserted score.
