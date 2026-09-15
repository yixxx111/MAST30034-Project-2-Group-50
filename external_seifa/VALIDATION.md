# Validation

Validation date: 15 September 2026.

## Actual source run

Inputs:

- Official ABS `Postal Area, Indexes, SEIFA 2021.xlsx` workbook.
- Local pipe-delimited `tables/tbl_consumer.csv`, using only the `postcode` column.

Observed results:

- Source workbook hash: `7b001c4a675d8f26c7dd48192ce724a8cba54948124b171058c40ab600edda6c`.
- 2,643 published POAs reconciled before excluding the two special geographies.
- 2,641 ordinary POAs and 59 output columns.
- 2,624 rows with all four scores, 3 with partial scores and 14 with no scores.
- IRSD, IRSAD and IER each contain 2,624 scored POAs; IEO contains 2,627.
- 167 scored POAs carry the ABS low-SA1-representation caution flag.
- 15 scored POAs cross state or territory boundaries.
- State ranks are absent for 18 rows per index: 15 Cross Border and 3 Other Territories rows.
- No final numeric range violations.
- Zero score/decile mismatches between the summary and four detail tables.
- No imputation, score trimming, winsorisation or outlier deletion.
- Independent rerun produced byte-identical clean CSV and Parquet files.

## Consumer postcode audit

- Total rows: 499,999.
- Geography matched: 416,818 (83.36%).
- All four scores available: 414,125 rows (82.83% of all consumers).
- Partial scores available: 472 rows (0.09%).
- Geography matched but no score available: 2,221 rows (0.44%).
- Postcode absent from the SEIFA dimension: 83,181 rows (16.64%).
- Missing or malformed consumer postcodes: 0.
- Consumer input rows removed: 0.

## Automated tests

Seven tests passed. The suite covers:

- leading-zero postcode preservation and malformed input rejection;
- retention of ordinary POAs with partial scores;
- separation of score unavailability from postcode join failure;
- consumer LEFT JOIN row preservation;
- reading only the consumer postcode field;
- typed CSV/Parquet output and source immutability;
- duplicate dimension rejection;
- transaction enrichment row preservation and retention of Member 2 core fields.

Run from the project root:

```powershell
python -m pytest external_seifa/tests -q
```

## Notebook

- 10 cells total, including 5 executed code cells.
- Zero execution errors.
- Two chart outputs: consumer match status and the four national percentile distributions.
- A standalone HTML copy is saved with code inputs hidden.

## Limits

- The full Member 2 transaction dataset was not enriched in this saved run because it is not present in this local project folder.
- Consumer coverage is consumer-row weighted, not transaction-count or transaction-value weighted.
- POA is not an exact delivery-postcode geography.
- The curation stage does not decide merchant scoring weights or validate a predictive model.
