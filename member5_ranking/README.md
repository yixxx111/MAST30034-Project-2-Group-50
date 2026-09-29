# Member 5 — Merchant ranking and recommendations

FinalScore is a **decision-support ranking, not a profit prediction**.
**FraudSafety is not a probability of no fraud. KNN confidence is not prediction accuracy.**

## Candidate pool and locked method

There are 4,026 eligible merchants out of 4,422 observed merchants. The 396 excluded merchants
lack necessary merchant master information (including category/take rate); they have not been
shown to be unsuitable for onboarding. Obtain their missing data before comparable scoring.

BusinessScore = 0.30 Value + 0.25 Customer Reach + 0.20 Growth + 0.15 Stability + 0.10 Market Context.
FinalScore = 0.90 BusinessScore + 0.10 FraudSafety.

| Component | Actual input | Preferred direction | Effective final weight |
|---|---|---|---|
| Value | `estimated_bnpl_revenue` | Higher | 27% |
| Customer Reach | `unique_consumers` | Higher | 22.5% |
| Growth | `normalized_monthly_revenue_trend` | Higher | 18% |
| Stability | `monthly_revenue_cv` | Lower (reversed percentile) | 13.5% |
| Market Context | `seifa_irsad_national_decile` | Higher, a chosen contextual preference | 9% |
| FraudSafety | `risk_safety_score` | Higher | 10% |

Business inputs become percentile scores within the eligible pool. Missing growth/stability scores
receive the existing neutral value 50. Finite low-sample estimates remain scored. Transaction count,
repeat-consumer share, Census and ATO features are diagnostics, not extra weighted inputs.
CustomerScore measures historical reach, not loyalty or repeat share.

Fraud is already integrated. The baseline risk index is 50% `consumer_exposure_percentile` +
50% `knn_merchant_risk_percentile`, with FraudSafety = 100 × (1 − risk index). These upstream
percentiles use the Member 4 pool of 4,422 merchants. For seven eligible merchants without consumer
exposure, the supplied KNN-only fallback is preserved. Member 5 performs no new fraud imputation.
Required missing risk/reliability inputs stop execution.

The weights express business judgment, not statistical optimality. Higher scores indicate alignment
with these preferences; score differences are not differences in profit or acceptance probabilities.

## Field interpretation and time scope

| Field | Meaning and limitation |
|---|---|
| `estimated_bnpl_revenue` | Observed transaction value × take rate / 100; historical commercial value proxy, not future revenue, incremental sales or net profit. |
| `unique_consumers` | Distinct historical consumers per merchant; not necessarily new customers. Counts cannot be summed into deduplicated portfolio reach. |
| `monthly_revenue_cv` | Standard deviation / mean monthly revenue; lower means less observed volatility. |
| `normalized_monthly_revenue_trend` | Normalized regression slope per month; not month-on-month percentage growth or a forecast. |
| `seifa_irsad_national_decile` | Transaction-weighted customer-postcode regional advantage, not merchant location or personal income. |
| `fraud_risk_index` | Combined relative model signal, not an observed fraud rate or proof of fraud. |
| `risk_safety_score` | FraudSafety, a relative safety index on a 0–100 scale, not no-fraud probability. |
| `knn_confidence_score` | 1 minus a relative neighbour-distance percentile: similarity/support, not accuracy or calibrated confidence. |
| `knn_out_of_distribution` | Mean neighbour distance exceeds the labelled reference sample's 95th percentile; diagnostic, not an automatic penalty. |
| `low_sample_growth_estimate` | Thin-history diagnostic; not an automatic penalty on finite scores. |
| `balanced_rank`, `final_rank` | Business-only and fraud-adjusted ranks, respectively; smaller is better. |
| `rank_change_after_fraud` | Business rank minus final rank; positive means an improved position. |
| `regional_data_coverage_rate_all_sources_by_count` | Joint external-source postcode match coverage by transaction count; a match does not guarantee every field is usable. |

- Commercial totals/reach use the supplied history, 2021-02-28 to 2022-10-26.
- Growth/CV use full months 2021-03 through 2022-09, filling gaps only within each merchant's
  first-to-last activity span. Later silent months after its final transaction are not included.
- Fraud behavioural features/exposure use transactions through 2022-02-28. Only 61 merchants have
  direct merchant labels; supplied risk labels are not confirmed transaction-level fraud outcomes.
- Census/SEIFA refer to 2021; ATO to 2021–22 and was published later. This is a retrospective,
  mixed-window analysis, not a common-date forward backtest. Refresh evidence for live onboarding.
- External matching is incomplete and varies by state. Coverage similarity does not prove absence
  of missingness bias. Do not infer personal creditworthiness or fraud from regional advantage.

## Inputs and execution order

Preserve the existing upstream workflow; these enhancements do not modify Members 1–4.
Required prepared inputs:

1. `member3_merchant_features/results/merchant_features.parquet`
2. `member4_fraud/result/knn_fraud_risk_scores.csv`
3. `member3_industry_growth/results/category_to_group_mapping.csv`

Use a Python kernel with pandas, NumPy, PyArrow and IPython/ipykernel. The tested package versions
are recorded in `requirements.txt`; nbformat/nbclient support automated fresh-kernel execution.
Run from the repository root or `member5_ranking/`:

1. Restart kernel and Run All in `ranking_summary.ipynb` to produce `ranking_analysis_base.csv`
   and the existing ranking diagnostics.
2. Restart kernel and Run All in `final_recommendations.ipynb` to produce final lists and the
   Essential explanatory tables. Run this second notebook after regenerating the ranking base.

Both notebooks contain consistency assertions; do not reuse stale outputs after a failed run.

## Results (`member5_ranking/results/`)

Existing outputs are retained:

| File | Purpose |
|---|---|
| `feature_correlation_matrix.csv` | Candidate-feature correlation diagnostic |
| `preliminary_top_20.csv` | Business-only Top20; the legacy name denotes a stage, not unfinished fraud integration |
| `fraud_for_ranking.csv` | Validated upstream fraud inputs |
| `fraud_composition_final_ranking_sensitivity.csv` | Existing 70/30, 50/50 and 30/70 composition scenarios |
| `top100_knn_reliability_summary.csv` | KNN support/OOD diagnostic |
| `ranking_analysis_base.csv` | Full eligible ranking and features consumed by recommendations |
| `final_top20_inspection.csv` | Final Top20 inspection |
| `final_top_100.csv` | Overall 100-slot shortlist |
| `segment_top_10.csv` | Ten merchants in each of five industry views |
| `segment_summary.csv` | Existing segment score summary |

Essential additions:

| File | Business question |
|---|---|
| `top100_profile_summary.csv` | How do selected merchants differ in raw historical value, reach, stability, growth, relative risk and ticket size? Includes sample/missing counts, median and Q1/Q3. |
| `top100_score_gap.csv` | How do the selected score profiles differ? Descriptive, not independent feature importance. |
| `merchant_case_studies.csv` | How should a strong, manual-review and non-selected boundary candidate be treated? |
| `merchant_case_score_contributions.csv` | How do the unchanged weighted components sum to each case's final score? Arithmetic explanation, not causal attribution. |
| `segment_business_summary.csv` | What does each industry contribute, with eligible denominators, selection rates, Top10 raw profiles and review cautions? |

## Stakeholder usage and limitations

Prioritize commercially strong selected merchants with adequate observed history and stronger model
support for ordinary due diligence. Selected OOD merchants, thin-history cases and membership-sensitive
cases in existing sensitivity results merit manual review of current commercial/risk evidence. Flags
are review cues, not new approval thresholds or automatic rejection rules. Non-OOD does not certify safety.

The cases are Interdum Feugiat Sed Inc. (strong), Tellus Id Institute (selected but OOD), and Non Magna
Nam PC (rank 101 reserve). Nisl Arcu Iaculis Incorporated (rank 77) was inspected but would duplicate the
selected/OOD example; a genuinely non-selected case better explains the capacity boundary. A small score
gap at rank 100 does not establish a meaningful probability difference or commercial unsuitability.

All 50 segment-level Top10 merchants are already in the overall Top100. They are an **industry view of
the overall strong candidate pool, not 50 additional onboarding slots**. There are no industry-specific
weights. Top10 medians describe selected leaders, not entire industries. Similar selected mean scores
cannot demonstrate absence of industry bias; selection-rate differences alone also do not prove bias.

Existing commercial-weight sensitivity is business-only: its 66 common merchants are not a verified
final-ranking robust core. Existing fraud-composition and fraud-weight scenarios concern final ranking.
Sensitivity describes recommendation robustness to selected assumptions; it cannot prove a correct
ranking, predict future profitability, or substitute for validation on future outcomes.

The Essential additions explain the locked method and its practical use. They add no value-only baseline,
unified final-ranking sensitivity, concentration analysis, MarketScore ablation, deep ranks 101–200
analysis, or individual merchant trend charts.
