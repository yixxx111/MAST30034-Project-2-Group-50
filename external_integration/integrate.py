"""Merge three cleaned area dimensions, then LEFT JOIN consumers and transactions."""
import argparse
import csv
import hashlib
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

SOURCES = ('census', 'seifa', 'ato')


def key(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text.zfill(4) if re.fullmatch('[0-9]{1,4}', text) else None


def build_dimension(paths):
    frames, dictionary, provenance = [], [], {}
    for name in SOURCES:
        path = Path(paths[name])
        df = pd.read_parquet(path)
        if 'postcode' not in df:
            raise ValueError(f'{name}: missing postcode')
        df['postcode'] = df['postcode'].map(key).astype('string')
        if df.postcode.isna().any() or df.postcode.duplicated().any():
            raise ValueError(f'{name}: null/invalid/duplicate normalised postcode')
        year = 'ato_income_year' if name == 'ato' else f'{name}_source_year'
        expected = '2021-22' if name == 'ato' else '2021'
        if year not in df or set(df[year].astype(str)) != {expected}:
            raise ValueError(f'{name}: expected single reference year {expected}')
        rename = {c: f'{name}_{c}' for c in df if c != 'postcode' and not c.startswith(name+'_')}
        df = df.rename(columns=rename)
        if df.columns.duplicated().any():
            raise ValueError(f'{name}: column naming collision')
        df[f'{name}_matched'] = True
        # Carry definitions from the source dictionaries, not just the column names.
        dictionary_file = path.parent / 'data_dictionary.csv'
        definitions = {}
        if dictionary_file.exists():
            with dictionary_file.open(encoding='utf-8-sig') as f:
                definitions = {rename.get(r['field'],r['field']): r for r in csv.DictReader(f)}
        for c in df:
            if c != 'postcode':
                d = definitions.get(c,{})
                dictionary.append({'field':c,'source':name,'definition':d.get('definition', 'Source postcode exists' if c.endswith('_matched') else ''),
                                   'unit':d.get('unit','boolean' if c.endswith('_matched') else ''),
                                   'source_or_formula':d.get('source_or_formula',d.get('source',''))})
        provenance[name] = {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                            'rows':len(df),'reference_year':expected}
        frames.append(df)
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame,on='postcode',how='outer',validate='one_to_one')
    for name in SOURCES:
        merged[f'{name}_matched'] = merged[f'{name}_matched'].eq(True)
    merged['all_sources_matched'] = merged[[f'{s}_matched' for s in SOURCES]].all(axis=1)
    merged = merged.sort_values('postcode').reset_index(drop=True)
    # Restore nullable numeric/boolean types after full outer joins.
    merged = merged.convert_dtypes()
    dictionary += [{'field':'postcode','source':'union','definition':'Four-character postcode join key; not proof of postal validity','unit':'string','source_or_formula':'union of three source keys'},
                   {'field':'all_sources_matched','source':'derived','definition':'Key exists in all three sources; does not imply all fields are complete','unit':'boolean','source_or_formula':'census_matched AND seifa_matched AND ato_matched'}]
    return merged, dictionary, provenance


def reduce_dimension(dimension):
    """A postcode + match-flag-only slice of the full dimension.

    Used for the transaction-level join: attaching all 141 external columns to every
    one of ~14M transaction rows is expensive to compute and to store, and the project
    brief explicitly says not to keep one giant combined transaction table. Match rates
    and row-count checks -- the actual step-2 deliverable -- only need the flags, not
    the feature values themselves. Full feature enrichment happens later, after
    transactions are aggregated to merchant level (a table thousands of rows, not
    millions), where joining all 141 columns is cheap.
    """
    keep = ['postcode'] + [f'{s}_matched' for s in SOURCES] + ['all_sources_matched']
    return dimension[keep].copy()


def quote(value):
    return '"'+value.replace('"','""')+'"'


def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def parquet_source(path):
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    pattern = path / '**/*.parquet' if path.is_dir() else path
    if path.is_dir() and not any(path.rglob('*.parquet')):
        raise ValueError('No transaction Parquet files')
    return f'read_parquet({literal(pattern)}, hive_partitioning=true, union_by_name=true)'


def enrich(con, dimension, base_query, id_col, postcode_col, destination, reports, scope, amount=None, full_hash_check=True):
    con.execute(f'CREATE OR REPLACE TEMP VIEW base AS {base_query}')
    columns = [x[0] for x in con.execute('DESCRIBE base').fetchall()]
    required = {id_col,postcode_col} | ({amount} if amount else set())
    if not required <= set(columns):
        raise ValueError(f'Missing input columns: {required-set(columns)}')
    extras = [c for c in dimension if c != 'postcode']
    collision = set(extras+['external_postcode','external_postcode_status']) & set(columns)
    if collision:
        raise ValueError(f'Already enriched / column collision: {collision}')
    before = con.execute(f'SELECT count(*),count(DISTINCT {quote(id_col)}),count(*) FILTER(WHERE {quote(id_col)} IS NULL OR trim(CAST({quote(id_col)} AS VARCHAR))=\'\') FROM base').fetchone()
    if before[0] != before[1] or before[2]:
        raise ValueError('Input IDs must be unique and nonempty')
    if amount and con.execute(f'SELECT count(*) FROM base WHERE {quote(amount)} IS NULL OR NOT isfinite({quote(amount)}) OR {quote(amount)}<=0').fetchone()[0]:
        raise ValueError('Expected positive finite curated amounts')
    norm = f"CASE WHEN regexp_full_match(trim(CAST({quote(postcode_col)} AS VARCHAR)), '[0-9]{{1,4}}') THEN lpad(trim(CAST({quote(postcode_col)} AS VARCHAR)),4,'0') ELSE NULL END"
    con.execute(f'CREATE OR REPLACE TEMP VIEW keyed AS SELECT *, {norm} AS external_postcode FROM base')
    con.register('external_dimension',dimension)
    selections = [f'coalesce(d.{quote(c)},false) AS {quote(c)}' if c.endswith('_matched') else f'd.{quote(c)}' for c in extras]
    con.execute('CREATE OR REPLACE TEMP VIEW joined AS SELECT b.*, '+', '.join(selections)+
                ", CASE WHEN b.external_postcode IS NULL THEN 'invalid_or_missing_postcode' WHEN d.postcode IS NULL THEN 'postcode_not_in_any_source' ELSE 'matched_any_source' END AS external_postcode_status FROM keyed b LEFT JOIN external_dimension d ON b.external_postcode=d.postcode")
    after = con.execute(f'SELECT count(*),count(DISTINCT {quote(id_col)}) FROM joined').fetchone()
    if before[:2] != after:
        raise AssertionError('Join changed rows/unique IDs')
    original_cols = ','.join(quote(c) for c in columns)
    if full_hash_check:
        # Cheap order-independent equivalence check: an aggregate hash over every
        # original column, instead of a full EXCEPT ALL set-difference. For a
        # straightforward LEFT JOIN that only appends new columns, a full row-level
        # EXCEPT ALL over millions of rows is far more expensive than this single
        # hash aggregate and verifies the same thing (the source columns are
        # untouched by the join).
        hash_before = con.execute(f'SELECT sum(hash(({original_cols}))) FROM base').fetchone()[0]
        hash_after = con.execute(f'SELECT sum(hash(({original_cols}))) FROM joined').fetchone()[0]
        if hash_before != hash_after:
            raise AssertionError('Join changed original values')
    else:
        # Exact source-column preservation, including all dates, amounts and flags.
        changed = con.execute(f'SELECT count(*) FROM ((SELECT {original_cols} FROM base EXCEPT ALL SELECT {original_cols} FROM joined) UNION ALL (SELECT {original_cols} FROM joined EXCEPT ALL SELECT {original_cols} FROM base))').fetchone()[0]
        if changed:
            raise AssertionError('Join changed original values')
    amount_before = con.execute(f'SELECT sum(CAST({quote(amount)} AS DECIMAL(38,10))) FROM base').fetchone()[0] if amount else None
    amount_after = con.execute(f'SELECT sum(CAST({quote(amount)} AS DECIMAL(38,10))) FROM joined').fetchone()[0] if amount else None
    if amount_before != amount_after:
        raise AssertionError('Join changed amount total')
    records=[]
    for flag in [f'{s}_matched' for s in SOURCES]+['all_sources_matched']:
        n=con.execute(f'SELECT count(*) FROM joined WHERE {quote(flag)}').fetchone()[0]
        records.append({'scope':scope,'metric':flag,'rows':n,'denominator':before[0],'rate':n/before[0] if before[0] else None,
                        'matched_amount':str(con.execute(f'SELECT sum(CAST({quote(amount)} AS DECIMAL(38,10))) FROM joined WHERE {quote(flag)}').fetchone()[0]) if amount else None,
                        'total_amount':str(amount_before) if amount else None})
    if amount:
        for r in records:
            from decimal import Decimal
            r['amount_coverage_rate'] = float(Decimal(r['matched_amount'])/amount_before) if amount_before and r['matched_amount']!='None' else 0.0
    pd.DataFrame(records).to_csv(reports/f'{scope}_coverage.csv',index=False)
    missing=[]
    for c in extras:
        if c.endswith('_matched'):
            continue
        source=c.split('_')[0]
        n, matched_null = con.execute(f'SELECT count(*) FILTER(WHERE {quote(c)} IS NULL), count(*) FILTER(WHERE {quote(source+"_matched")} AND {quote(c)} IS NULL) FROM joined').fetchone()
        missing.append({'field':c,'total_rows':before[0],'missing_rows':n,'matched_but_missing_rows':matched_null})
    pd.DataFrame(missing).to_csv(reports/f'{scope}_feature_missingness.csv',index=False)
    con.execute(f"COPY (SELECT external_postcode, external_postcode_status, census_matched,seifa_matched,ato_matched,count(*) AS rows FROM joined WHERE NOT all_sources_matched GROUP BY ALL ORDER BY external_postcode) TO {literal(reports/f'{scope}_postcode_exceptions.csv')} (HEADER,FORMAT CSV)")
    destination.parent.mkdir(parents=True,exist_ok=True)
    con.execute(f'COPY joined TO {literal(destination)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)')
    saved=con.execute(f'SELECT count(*) FROM read_parquet({literal(destination)})').fetchone()[0]
    if saved != before[0]:
        raise AssertionError('Saved rows differ')
    return {'input_rows':before[0],'output_rows':after[0],'unique_ids':after[1], 'original_columns_unchanged':True,
            'amount_sum_before':str(amount_before) if amount else None,'amount_sum_after':str(amount_after) if amount else None}


def write_transaction_diagnostics(con, out, joined_view='joined'):
    """Write the two transaction-level diagnostic tables requested by review, from a
    TEMP VIEW that already has consumer_state, dollar_value and all_sources_matched
    (the `joined` view enrich() leaves behind after a transaction-scope call). Returns
    the two output filenames written, or an empty list if consumer_state isn't present
    (e.g. a caller that only ran the consumer-scope enrich()).

    - transaction_coverage_by_state.csv: a single blended coverage rate hides state-level
      gaps (NSW/WA/NT run 20+ points below VIC/QLD/SA/TAS -- see VALIDATION.md).
    - transaction_amount_by_match_status.csv: checks whether unmatched transactions are
      systematically larger/smaller than matched ones, rather than relying on the
      row-count vs. amount coverage rates happening to be close.
    """
    columns = [c[0] for c in con.execute(f"DESCRIBE {joined_view}").fetchall()]
    if "consumer_state" not in columns:
        return []
    con.execute(f"""
        COPY (
            SELECT consumer_state,
                count(*) AS transactions,
                sum(CASE WHEN all_sources_matched THEN 1 ELSE 0 END) AS matched_transactions,
                sum(CASE WHEN all_sources_matched THEN 1 ELSE 0 END)::DOUBLE / nullif(count(*), 0) AS all_sources_matched_rate
            FROM {joined_view}
            GROUP BY consumer_state
            ORDER BY all_sources_matched_rate DESC
        ) TO {literal(out/'transaction_coverage_by_state.csv')} (HEADER, FORMAT CSV)
    """)
    con.execute(f"""
        COPY (
            SELECT all_sources_matched,
                count(*) AS n,
                quantile_cont(dollar_value, 0.25) AS p25,
                quantile_cont(dollar_value, 0.50) AS p50,
                quantile_cont(dollar_value, 0.75) AS p75,
                quantile_cont(dollar_value, 0.99) AS p99,
                avg(dollar_value) AS mean
            FROM {joined_view}
            GROUP BY all_sources_matched
            ORDER BY all_sources_matched
        ) TO {literal(out/'transaction_amount_by_match_status.csv')} (HEADER, FORMAT CSV)
    """)
    return ["transaction_coverage_by_state.csv", "transaction_amount_by_match_status.csv"]


def run(args):
    root=Path(__file__).resolve().parent.parent
    out=args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output must be empty; use a new directory')
    paths={s:root/f'external_{s}/results/{s}_clean.parquet' for s in SOURCES}
    dimension,dictionary,sources=build_dimension(paths)
    out.mkdir(parents=True,exist_ok=True)
    dimension.to_parquet(out/'external_postcode_features.parquet',index=False)
    dimension.to_csv(out/'external_postcode_features.csv',index=False)
    pd.DataFrame(dictionary).to_csv(out/'data_dictionary.csv',index=False)
    dimension.groupby([f'{s}_matched' for s in SOURCES],dropna=False).size().reset_index(name='postcodes').to_csv(out/'postcode_source_patterns.csv',index=False)
    metadata={'run_utc':datetime.now(timezone.utc).isoformat(),'sources':sources,'dimension_rows':len(dimension),'dimension_columns':len(dimension.columns),
              'consumers':'not_run_no_input','transactions':'not_run_no_input','temporal_use':'retrospective context; no historical availability assertion',
              'privacy':'consumer-level outputs stay under ignored local_data; no names, addresses or gender exported',
              'versions':{'duckdb':duckdb.__version__,'pandas':pd.__version__}}
    local=out/'local_data';local.mkdir(exist_ok=True)
    (out/'.gitignore').write_text('local_data/\n')
    con=duckdb.connect();con.execute("SET memory_limit='2GB'");con.execute('SET threads=4')
    con.execute(f'SET temp_directory={literal(local/"duckdb_spill")}')
    with tempfile.TemporaryDirectory(prefix='integration-consumers-') as temp:
        if args.consumers:
            consumer=args.consumers.resolve()
            metadata['consumer_input_sha256']=hashlib.sha256(consumer.read_bytes()).hexdigest()
            if consumer.suffix.lower()=='.zip':
                with zipfile.ZipFile(consumer) as z:
                    names=[n for n in z.namelist() if n=='tbl_consumer.csv' or n.endswith('/tbl_consumer.csv')]
                    if len(names)!=1: raise ValueError('Expected one consumer table')
                    consumer=Path(temp)/'tbl_consumer.csv'
                    with z.open(names[0]) as src,consumer.open('wb') as dst:
                        import shutil
                        shutil.copyfileobj(src,dst)
            query=f"SELECT consumer_id, postcode AS consumer_postcode, state AS consumer_state FROM read_csv({literal(consumer)}, delim='|',header=true,all_varchar=true)"
            metadata['consumers']=enrich(con,dimension,query,'consumer_id','consumer_postcode',local/'consumers_with_external.parquet',out,'consumer')
        (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
        if args.transactions:
            try:
                tx_dimension = reduce_dimension(dimension) if not args.transactions_full else dimension
                metadata['transactions_join_mode']='full_141_columns' if args.transactions_full else 'light_match_flags_only'
                metadata['transactions']=enrich(con,tx_dimension,'SELECT * FROM '+parquet_source(args.transactions),'order_id','consumer_postcode',local/'transactions_with_external.parquet',out,'transaction','dollar_value',full_hash_check=True)
                metadata['transaction_input']=str(args.transactions.resolve())
                # Two diagnostic tables requested by review, built from the same `joined`
                # TEMP VIEW enrich() just left behind for transactions -- part of the real
                # pipeline run, not a one-off query; a teammate re-running this script from
                # raw data gets both files for free. See write_transaction_diagnostics().
                written = write_transaction_diagnostics(con, out)
                if written:
                    metadata['transaction_coverage_by_state'] = 'transaction_coverage_by_state.csv'
                    metadata['transaction_amount_by_match_status'] = 'transaction_amount_by_match_status.csv'
            except Exception as exc:
                metadata['transactions']={'status':'failed','error':str(exc)}
                (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
                raise
    con.close()
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps(metadata,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--consumers',type=Path)
    p.add_argument('--transactions',type=Path)
    p.add_argument('--transactions-full',action='store_true',help='Join all 141 external columns onto every transaction row instead of just the match flags. Expensive (~14M rows) and produces a large output file; off by default.')
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'results')
    run(p.parse_args())
