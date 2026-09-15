"""Single entry point for Census cleaning, consumer audit and optional transaction join."""
import argparse
import json
from pathlib import Path
from .curation import run_pipeline


def main():
    parser = argparse.ArgumentParser(description='Clean ABS 2021 POA ZIP and audit consumer coverage.')
    parser.add_argument('--zip',required=True,type=Path,dest='zip_path')
    parser.add_argument('--output-root',type=Path,default=Path('data/curated/census'))
    parser.add_argument('--consumer-csv',type=Path)
    parser.add_argument('--consumer-postcode-column',default='postcode')
    parser.add_argument('--curated-transactions',type=Path,help='Optional Member 2 Parquet file or partitioned directory')
    parser.add_argument('--enriched-output-root',type=Path,help='Required when --curated-transactions is supplied')
    args = parser.parse_args()
    if bool(args.curated_transactions)!=bool(args.enriched_output_root):
        parser.error('--curated-transactions and --enriched-output-root must be supplied together')
    result = run_pipeline(args.zip_path,args.output_root,args.consumer_csv,args.consumer_postcode_column)
    if args.curated_transactions:
        from .enrich_transactions import enrich_transactions
        result['transaction_enrichment'] = enrich_transactions(args.curated_transactions,args.output_root/'census_clean.parquet',args.enriched_output_root)
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
