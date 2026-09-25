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
- `fraud_weight_sensitivity_summary.csv`: rank correlation and Top-100 overlap across the three component-weight scenarios.
- `knn_fraud_risk_scores.csv`: KNN score, distance confidence, KNN percentile, and three consumer/KNN weighting scenarios (70/30, 50/50, and 30/70).
- `consumer_model_comparison.csv` and `merchant_model_comparison.csv`: model selection evidence.
- `merchant_nested_cv_summary.csv`: outer-fold estimate of the complete KNN tuning procedure.
- `merchant_loocv_summary.csv`: leave-one-merchant-out sensitivity metrics for the selected pipeline.
- `merchant_selected_features.csv`: features retained by fold-local mutual-information selection.
- `merchant_nested_feature_stability.csv`: how often each feature was selected across outer folds.

## Interpretation and limitations

The consumer target is a supplied probability score, not a confirmed fraud event. Consumer predictions are end-of-day risk estimates and cannot be attributed to an individual transaction when a consumer used multiple merchants that day. Only 61 merchants have direct merchant-fraud observations, so the merchant model is highly uncertain. The final `fraud_risk_index` is a relative review-prioritisation index, not a fraud probability or proof of wrongdoing.

The combined risk profile uses an amount-weighted consumer exposure percentile and a KNN merchant-risk percentile. It reports 70% consumer/30% KNN, 50% consumer/50% KNN, and 30% consumer/70% KNN sensitivity scenarios. `fraud_risk_index` and `risk_safety_score` remain aliases for the neutral 50/50 scenario. To avoid double counting, KNN uses merchant behavioural features only; consumer-risk exposure features are excluded from KNN and introduced after prediction. Inside each cross-validation fold, the KNN pipeline applies median imputation, compares standard, robust, and Yeo-Johnson power transformations, and performs mutual-information feature selection. Repeated cross-validation selects transformation, feature count, neighbour count, Manhattan or Euclidean distance, and weighting using mean Spearman rank correlation first, with MAE and RMSE as tie-breakers. Nested cross-validation estimates the complete selection procedure, and LOOCV is retained as a sensitivity check. The 13-merchant holdout is not used for selection.

Distance diagnostics compare every scored merchant with leave-self-out neighbour distances among the 61 labelled merchants. `knn_confidence_score` is a relative support measure, not a statistical probability. `knn_out_of_distribution` identifies merchants whose mean neighbour distance is above the labelled sample's 95th percentile. To prevent self-neighbour leakage, the 61 labelled merchants receive leave-one-out final KNN scores; the other merchants use the model refitted on all labels. The neutral default gives the two risk percentiles equal weight, while the alternative columns support weight sensitivity checks; the sole available source is used when one component is missing. Direct merchant observations are training labels and diagnostics only, rather than a second manually inserted score.

The current run improves repeated and nested validation relative to the earlier fixed 14-feature KNN, but the 13-merchant holdout is mixed: MAE improves slightly while holdout Spearman is lower. Parameter choices also vary across outer folds. These checks indicate material small-sample uncertainty rather than conclusive superiority. In addition, 1,763 of 4,422 scored merchants are beyond the labelled sample's 95th-percentile neighbour-distance threshold. Downstream users should retain the confidence and out-of-distribution fields and avoid treating every KNN score as equally reliable.
