from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.curation import build_curated_transactions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the curated transaction dataset and audit artefacts."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Extracted tables directory. Defaults to PROJECT2_DATA_ROOT or ./tables.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/curated"),
        help="Directory for derived curation outputs (default: data/curated).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_curated_transactions(args.data_root, args.output_root)
    printable_result = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(result).items()
    }
    print(json.dumps(printable_result, indent=2))


if __name__ == "__main__":
    main()

