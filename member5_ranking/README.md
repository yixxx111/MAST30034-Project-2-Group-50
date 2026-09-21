# Member 5 - Merchant Ranking

## Goal
Build an interpretable merchant ranking system for BNPL onboarding.

## Candidate Pool
- Start from merchants with a valid merchant master record.
- Exclude transaction-only orphan merchants from the final recommendation list because category and take-rate information are unavailable.
- Fraud/risk features will be added once Member 4 completes the fraud module.

## Ranking Dimensions

### 1. BNPL Value
- estimated_bnpl_revenue
- total_revenue

### 2. Customer Strength
- repeat_consumer_share
- total_transactions

### 3. Growth
- normalized_monthly_revenue_trend

### 4. Stability
- monthly_revenue_cv

### 5. Market / Regional Context
- selected external Census / SEIFA / ATO features

### 6. Risk
- fraud_transaction_rate
- fraud_amount_rate
- merchant_fraud_probability
- to be added later

## Normalisation
Convert metrics to percentile scores from 0 to 100.
Reverse metrics where lower is better, e.g. revenue CV and fraud risk.

## Outputs
- Overall Top 100 merchants
- Top 10 merchants within each of the 5 industry segments
- Ranking diagnostics and sensitivity analysis