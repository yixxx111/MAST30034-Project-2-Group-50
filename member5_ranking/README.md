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
   Essential explanatory tables and concise Useful implications. Run this second notebook after regenerating the ranking base and Useful diagnostics.

Both notebooks contain consistency assertions; do not reuse stale outputs after a failed run.

## Results (`member5_ranking/results/`)

Existing outputs are retained:

| File | Purpose |
|---|---|
| `feature_correlation_matrix.csv` | Candidate-feature correlation diagnostic |
| `preliminary_top_20.csv` | Internal business-only inspection; not an official recommendation output |
| `fraud_for_ranking.csv` | Validated upstream fraud inputs |
| `fraud_composition_final_ranking_sensitivity.csv` | Existing 70/30, 50/50 and 30/70 composition scenarios |
| `top100_knn_reliability_summary.csv` | KNN support/OOD diagnostic |
| `ranking_analysis_base.csv` | Full eligible ranking and features consumed by recommendations |
| `final_top20_inspection.csv` | Internal inspection only; not an official recommendation output |
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

The original commercial-weight sensitivity is business-only (66 common merchants). Section 12 now
adds the same scenarios with a fixed baseline fraud adjustment (63 common merchants). These are distinct
analyses. Existing fraud-composition and fraud-weight scenarios concern final ranking.
Sensitivity describes recommendation robustness to selected assumptions; it cannot prove a correct
ranking, predict future profitability, or substitute for validation on future outcomes.

## Useful diagnostics (sections 11–14 of ranking_summary.ipynb)

Official recommendation outputs are **overall Top100 and Top10 within each of five segments**.
Top20 files are retained for internal inspection only. Concentration Top5/10/20 denotes cumulative
historical revenue shares within the already selected Top100, not additional recommendation lists.

1. **Revenue-only comparator:** select 100 by historical `estimated_bnpl_revenue` in the same eligible
   pool. Compare whole lists and non-overlapping groups using medians, Q1/Q3 and missing counts.
   Total historical proxy is included; customer counts are not summed into unique portfolio customers.
   Four illustrative cases cover high-ranking trade-offs and the selection boundary, with arithmetic
   score contributions. These are not literal one-to-one slot substitutions or proof of superiority.
2. **Final business scenarios:** reuse exactly the existing balanced (30/25/20/15/10), value-focused
   (40/25/15/15/5) and growth-focused (25/20/30/15/10) weights in Value/Reach/Growth/Stability/Market order.
   Apply 90% business + 10% unchanged 50/50 baseline FraudSafety to each. Report intersection, union,
   switchers and rank ranges. Near-cutoff means baseline ranks 80–120, an inspection window only.
   Review all selection-sensitive candidates plus current OOD/low-sample candidates. The list includes
   potential entrants and is not a recommendation to onboard extra merchants. OOD adds no penalty.
3. **Concentration:** calculate largest 5/10/20 revenue shares within the existing Top100 and partition
   that same pool by the existing five segments. Historical exposure is not future profit exposure;
   no optimal diversification or acceptable risk threshold is claimed.
4. **No-Market ablation:** set business Market weight to zero and proportionally redistribute its weight:
   Value 1/3, Reach 5/18, Growth 2/9, Stability 1/6. Keep final fraud weight at 10%. Output switchers,
   all-pool rank correlation and boundary rows (ranks 80–120 in either method, plus every switcher).
   Decompose score change into removed market points and redistributed business points. The closest
   raw-IRSAD pair of opposite switchers illustrates percentile conversion; it is not representative
   evidence about individuals or proof that the entire rank change is caused by raw IRSAD alone.

All diagnostics operate on copies. No official scoring weights, eligibility, normalization or lists
are changed. Cutoff ties trigger an assertion for review rather than silent membership expansion.
No deep ranks 101–200 study, new business-weight scenarios, or merchant trend charts are added.

| New result file in `results/` | Purpose |
|---|---|
| `value_only_baseline_comparison.csv` | Median/Q1/Q3, counts, overlap and historical proxy totals for four comparison groups |
| `value_only_baseline_membership.csv` | Identify shared and unique merchants, with both ranks and raw metrics |
| `value_only_replacement_cases.csv` | Four illustrative cases, raw evidence and weighted score points |
| `final_business_sensitivity_summary.csv` | Pairwise final overlap/correlation and conditional stability counts |
| `final_business_sensitivity_merchants.csv` | All eligible merchants' scenario ranks, selection counts, rank ranges and diagnostic flags |
| `final_business_sensitivity_review_list.csv` | Sensitive candidates and selected OOD/low-sample cases with review reasons |
| `top100_revenue_concentration.csv` | Largest 5/10/20 cumulative revenue-share statistics only |
| `top100_segment_revenue_contribution.csv` | Five-segment counts, historical proxy totals, shares and medians |
| `market_score_ablation_summary.csv` | Overlap/correlation, rank movement, raw IRSAD quartiles and diagnostic weights |
| `market_score_ablation_switchers.csv` | Every membership change, raw IRSAD, scores and score-change decomposition |
| `market_score_ablation_boundary.csv` | Affected boundary merchants including unchanged selections |
| `market_score_ablation_irsad_pair.csv` | Illustrative closest-IRSAD pair of opposite switchers |

The value-only comparison is an in-sample preference comparison, not predictive validation. The current
rule does not dominate revenue-only on every metric. Similar all-pool ranks can conceal material
membership changes at the cutoff. Neither these diagnostics nor sensitivity identifies optimal weights.
