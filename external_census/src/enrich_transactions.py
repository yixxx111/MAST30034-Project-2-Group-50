"""Optional DuckDB stage for Member 2's partitioned curated_transactions.

Writes a new dataset and separate join reports. Never updates the internal base.
"""
from __future__ import annotations
import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from .curation import DataQualityError, SPECIAL, sha256, validate_output, publish_directory


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _identifier(value):
    return '"' + value.replace('"','""') + '"'


def enrich_transactions(transactions, census_parquet, output_root):
    transactions,census_parquet,output_root = map(lambda p:Path(p).resolve(),[transactions,census_parquet,output_root])
    files = sorted(transactions.rglob('*.parquet')) if transactions.is_dir() else [transactions]
    if not files or any(not f.is_file() for f in files): raise FileNotFoundError('No input transaction Parquet files')
    validate_output(output_root,[transactions,census_parquet],marker='enrichment_metadata.json')
    # Do not write inside the input tree: it would enter the recursive glob on reruns.
    if transactions.is_dir() and transactions in output_root.parents:
        raise DataQualityError('Enriched output must be outside the input transaction directory')
    output_root.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.enrichment-',dir=output_root.parent))
    try:
        with duckdb.connect() as con:
            con.read_parquet([str(p) for p in files],hive_partitioning=True).create_view('source_transactions')
            con.read_parquet(str(census_parquet)).create_view('census')
            tc = [x[0] for x in con.execute('DESCRIBE source_transactions').fetchall()]
            cc = [x[0] for x in con.execute('DESCRIBE census').fetchall()]
            required = {'order_id','consumer_postcode'}
            if not required.issubset(tc): raise DataQualityError(f'Missing core fields: {sorted(required-set(tc))}')
            if not {'postcode','poa_code','census_source_year'}.issubset(cc): raise DataQualityError('Not a Census dimension')
            fields = [c for c in cc if c.startswith('census_')]
            added = fields+['census_postcode','census_poa_code','census_matched','census_match_status']
            if set(added)&set(tc): raise DataQualityError('Input already contains Census enrichment columns')
            bad = con.execute("""SELECT count(*) FROM census WHERE postcode IS NULL
                OR NOT regexp_full_match(CAST(postcode AS VARCHAR),'[0-9]{4}')
                OR postcode IN ('9494','9797')""").fetchone()[0]
            count,unique = con.execute('SELECT count(*),count(DISTINCT postcode) FROM census').fetchone()
            if not count or bad or count!=unique: raise DataQualityError('Census dimension has invalid or duplicate postcodes')
            before,orders = con.execute('SELECT count(*),count(DISTINCT order_id) FROM source_transactions').fetchone()
            if before!=orders: raise DataQualityError('Input must have non-null unique order_id, as promised by Member 2')
            feature_sql = ', '.join('c.'+_identifier(f) for f in fields)
            con.execute(f"""CREATE TEMP VIEW enriched AS
                WITH normalised AS (
                    SELECT *, CASE WHEN regexp_full_match(trim(CAST(consumer_postcode AS VARCHAR)),'[0-9]{{1,4}}')
                        THEN lpad(trim(CAST(consumer_postcode AS VARCHAR)),4,'0') ELSE NULL END AS census_postcode
                    FROM source_transactions)
                SELECT t.*, c.poa_code AS census_poa_code, {feature_sql},
                    c.postcode IS NOT NULL AS census_matched,
                    CASE WHEN t.census_postcode IS NULL THEN 'missing_or_invalid_postcode'
                         WHEN t.census_postcode IN ('9494','9797') THEN 'special_geography'
                         WHEN c.postcode IS NOT NULL THEN 'matched'
                         ELSE 'postcode_not_in_census' END AS census_match_status
                FROM normalised t LEFT JOIN census c ON t.census_postcode=c.postcode""")
            after = con.execute('SELECT count(*) FROM enriched').fetchone()[0]
            if before!=after: raise DataQualityError('External join changed transaction count')
            amount_sql = ', sum(dollar_value) AS dollar_value' if 'dollar_value' in tc else ''
            coverage = con.execute(f"""SELECT census_match_status, count(*) AS transaction_rows,
                count(*)::DOUBLE / NULLIF({before},0) AS transaction_rate {amount_sql}
                FROM enriched GROUP BY census_match_status ORDER BY census_match_status""").df()
            if 'dollar_value' in coverage:
                total = coverage.dollar_value.sum()
                coverage['dollar_value_rate'] = coverage.dollar_value/total if total else float('nan')
            coverage.to_csv(stage/'transaction_join_coverage.csv',index=False)
            missing_sql = ','.join(f'count(*) FILTER (WHERE {_identifier(f)} IS NULL)' for f in fields)
            matched_missing_sql = ','.join(f'count(*) FILTER (WHERE census_matched AND {_identifier(f)} IS NULL)' for f in fields)
            totals = con.execute(f'SELECT {missing_sql}, {matched_missing_sql} FROM enriched').fetchone()
            import pandas as pd
            pd.DataFrame([dict(field=f,all_transaction_missing=totals[i],matched_transaction_missing=totals[len(fields)+i])
                          for i,f in enumerate(fields)]).to_csv(stage/'transaction_feature_missingness.csv',index=False)
            con.execute(f"""COPY (SELECT census_postcode,census_match_status,count(*) AS transaction_rows
                FROM enriched WHERE NOT census_matched GROUP BY ALL)
                TO {_quote(stage/'transaction_postcode_exceptions.csv')} (HEADER, DELIMITER ',')""")
            out = stage/'curated_transactions_with_census.parquet'
            con.execute(f'COPY enriched TO {_quote(out)} (FORMAT PARQUET, COMPRESSION ZSTD)')
            saved = con.read_parquet(str(out)).aggregate('count(*)').fetchone()[0]
            if saved!=before: raise DataQualityError('Saved output row count differs')
        meta = dict(stage='census_transaction_enrichment',created_utc=datetime.now(timezone.utc).isoformat(),
                    input_rows=before,output_rows=after,input_partition_count=len(files),
                    census_sha256=sha256(census_parquet),join='many-to-one LEFT JOIN',
                    source_year=2021,base_modified=False,
                    output='curated_transactions_with_census.parquet',
                    note='Fraud and other external sources are unchanged. See separate original stage reports.')
        (stage/'enrichment_metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
        publish_directory(stage,output_root)
        return meta
    finally:
        if stage.exists(): shutil.rmtree(stage)


def main():
    p = argparse.ArgumentParser(description='Append Census features to a COPY of Member 2 curated Parquet.')
    p.add_argument('--transactions',required=True,type=Path)
    p.add_argument('--census-parquet',required=True,type=Path)
    p.add_argument('--output-root',required=True,type=Path)
    a = p.parse_args()
    print(json.dumps(enrich_transactions(a.transactions,a.census_parquet,a.output_root),indent=2))

if __name__=='__main__': main()
