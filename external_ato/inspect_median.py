"""Extract official Table 8 candidates separately; do not replace Table 6B features."""
import argparse
import hashlib
import json
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
from clean_ato import postcode, number, write_csv

URL = 'https://data.gov.au/data/dataset/4be150cc-8f84-46b8-8c61-55ff1d48a700/resource/9bd9d5af-2c09-405f-b484-69c862f4dc2e/download/ts22individual08medianaveragetaxableincomestatepostcode.xlsx'


def extract(raw, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    book = openpyxl.load_workbook(raw, read_only=True, data_only=True)
    sheet = list(book['Table 8'].values)
    notes = '\n'.join(' | '.join(str(v) for v in r if v is not None) for r in book['Notes'].values)
    headers = sheet[1]
    selected = ['Individuals 2021–22\nno.', 'Median3 taxable income 2021–22\n$', 'Average3 taxable income 2021–22\n$']
    indices = [headers.index(x) for x in selected]
    rows, seen = [], set()
    for row in sheet[2:]:
        if not any(v is not None for v in row):
            continue
        key = postcode(row[1])
        if key is None or key in seen:
            raise ValueError('Invalid/duplicate postcode in Table 8')
        seen.add(key)
        vals = []
        for i, index in enumerate(indices):
            value = row[index]
            if value == 'na':
                vals.append(None)
            else:
                value, reason = number(value, count=i == 0)
                if reason:
                    raise ValueError(f'Unexpected Table 8 value: {reason}')
                vals.append(value)
        rows.append({'postcode': key, 'ato_income_year': '2021-22', 'ato_table8_state': str(row[0]).strip(),
                     'ato_table8_individual_count': vals[0], 'ato_official_median_taxable_income': vals[1],
                     'ato_official_average_taxable_income': vals[2], 'ato_table8_available': vals[1] is not None})
    book.close()
    output.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: r['postcode'])
    write_csv(output / 'ato_table8_candidates.csv', rows)
    schema = pa.schema([('postcode', pa.string()), ('ato_income_year', pa.string()), ('ato_table8_state', pa.string()),
                        ('ato_table8_individual_count', pa.int64()), ('ato_official_median_taxable_income', pa.float64()),
                        ('ato_official_average_taxable_income', pa.float64()), ('ato_table8_available', pa.bool_())])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), output / 'ato_table8_candidates.parquet')
    (output / 'source_notes.txt').write_text(notes, encoding='utf-8')
    meta = {'source_url': URL, 'source_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
            'rows': len(rows), 'available_2021_22': sum(r['ato_table8_available'] for r in rows),
            'unavailable_2021_22': sum(not r['ato_table8_available'] for r in rows),
            'purpose': 'Optional candidate reference, not merged into ato_clean or used to choose ranking features',
            'na_policy': 'Preserved as null; no zero-fill or interpolation',
            'publication_rule': 'After 2013-14, statistics only included where more than 200 lodgments in relevant year',
            'population_note': 'From 2016-17 medians/averages use individuals who reported taxable income or loss label, including zero values',
            'caution': 'Do not infer Table 8 median from Table 6B totals; confirm cross-table average differences before substitution'}
    (output / 'metadata.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(json.dumps(meta, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--xlsx', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    extract(a.xlsx, a.output)
