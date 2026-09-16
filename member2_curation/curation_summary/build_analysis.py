from pathlib import Path
import argparse
import duckdb

def main():
    parser = argparse.ArgumentParser(description="Build aggregate evidence for the curation summary without changing transaction data.")
    parser.add_argument('--merchant-master', type=Path, help='Optional original tbl_merchants.parquet to diagnose ABN matching')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    data = here.parent / 'data' / 'curated'
    out = here / 'analysis_results'
    out.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute('SET threads=4')
    path = str(data/"curated_transactions/**/*.parquet").replace("'", "''")
    con.execute(f"CREATE VIEW tx AS SELECT * FROM read_parquet('{path}', hive_partitioning=true)")
    def save(name, sql):
        frame = con.execute(sql).df()
        frame.to_csv(out/name,index=False)
        return frame
    save('amount_summary.csv', """SELECT count(*) AS "rows", count(distinct order_id) unique_orders,
      min(dollar_value) minimum, quantile_cont(dollar_value,0.5) median,
      quantile_cont(dollar_value,0.95) p95, quantile_cont(dollar_value,0.99) p99,
      max(dollar_value) maximum, sum(dollar_value) total_value,
      count(*) FILTER(WHERE is_amount_above_p99) flagged_rows,
      sum(dollar_value) FILTER(WHERE is_amount_above_p99) flagged_value FROM tx""")
    save('merchant_match_impact.csv', """SELECT merchant_master_matched, count(*) transaction_count,
      count(distinct merchant_abn) merchants, sum(dollar_value) transaction_value,
      count(*) * 1.0 / (SELECT count(*) FROM tx) row_share,
      sum(dollar_value) / (SELECT sum(dollar_value) FROM tx) value_share FROM tx GROUP BY 1 ORDER BY 1 DESC""")
    save('amount_histogram.csv', """SELECT floor(log10(1+dollar_value)*20)/20 AS log10_amount_bin,
      count(*) transaction_count FROM tx GROUP BY 1 ORDER BY 1""")
    save('amount_flag_groups.csv', """SELECT is_amount_above_p99, count(*) transaction_count,
      min(dollar_value) minimum, max(dollar_value) maximum, sum(dollar_value) transaction_value
      FROM tx GROUP BY 1 ORDER BY 1""")
    if args.merchant_master:
        path = str(args.merchant_master).replace("'", "''")
        con.execute(f"CREATE VIEW master AS SELECT trim(cast(merchant_abn AS VARCHAR)) abn FROM read_parquet('{path}')")
        save('abn_diagnostic.csv', """WITH absent AS (SELECT DISTINCT trim(cast(merchant_abn AS VARCHAR)) abn
          FROM tx WHERE NOT merchant_master_matched)
          SELECT count(*) unmatched_abns,
          count(*) FILTER(WHERE regexp_full_match(abn,'[0-9]{11}')) eleven_digit_abns,
          count(*) FILTER(WHERE abn IN (SELECT abn FROM master)) exact_master_matches,
          count(*) FILTER(WHERE try_cast(abn AS BIGINT) IN
              (SELECT try_cast(abn AS BIGINT) FROM master)) numeric_master_matches
          FROM absent""")
    else:
        (out/'abn_diagnostic.csv').unlink(missing_ok=True)
    con.close()
if __name__ == '__main__': main()
