"""Command-line entry point for SEIFA cleaning and consumer coverage audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .curation import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean ABS SEIFA 2021 POA indexes.")
    parser.add_argument("--xlsx", required=True, type=Path, help="Official ABS Postal Area indexes workbook")
    parser.add_argument("--output-root", type=Path, default=Path("external_seifa/results"))
    parser.add_argument("--consumer-csv", type=Path)
    parser.add_argument("--consumer-postcode-column", default="postcode")
    args = parser.parse_args()
    result = run_pipeline(args.xlsx, args.output_root, args.consumer_csv, args.consumer_postcode_column)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
