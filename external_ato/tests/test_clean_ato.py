import csv
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clean_ato import (FIELDS, RAW_NAME, SMALL_DENOMINATOR, clean, consumer_counts, coverage, divide, number,
                       po_box_lvr_range, postcode, reconcile_table_6a, assert_reconciliation, write_parquet)


class CleaningTests(unittest.TestCase):
    def fixture(self):
        headers = ['State/ Territory1', 'Statistical Area Level 4 (SA4)2', 'Postcode'] + [x[0] for x in FIELDS.values()]
        # Counts differ deliberately: taxable label count is 80, all individuals is 100.
        row = ['NT', 'Darwin', 800, 100, 80, 8000, 50, 5000, 60, 600]
        return headers, row

    def test_postcode(self):
        self.assertEqual(postcode(800), '0800')
        self.assertEqual(postcode(' 800 '), '0800')
        for value in ['800.0', 'POA0800', 'NSW other', '0000', '5', 199, None, 800.5, True]:
            self.assertIsNone(postcode(value))

    def test_denominator_and_negative_income(self):
        h, r = self.fixture()
        rows, _, _ = clean(h, [r])
        self.assertEqual(rows[0]['ato_taxable_income_or_loss_per_reporter'], 100)
        self.assertEqual(rows[0]['ato_salary_recipient_share'], .5)
        self.assertEqual(rows[0]['ato_net_tax_payer_share'], .6)
        r[5] = -800
        rows, _, _ = clean(h, [r])
        self.assertEqual(rows[0]['ato_taxable_income_or_loss_per_reporter'], -10)
        self.assertTrue(rows[0]['ato_nonpositive_taxable_income_total'])

    def test_zero_and_suppression(self):
        self.assertIsNone(divide(20, 0)[0])
        self.assertIsNone(divide(120, 100, share=True)[0])
        self.assertEqual(number(0), (0, None))
        self.assertEqual(number(-1, count=True)[1], 'invalid_count')
        with self.assertRaises(ValueError):
            number('np')

    def test_duplicate_and_schema(self):
        h, r = self.fixture()
        with self.assertRaises(ValueError):
            clean(h, [r, r])
        with self.assertRaises(ValueError):
            clean(h[:-1], [r])

    def test_aggregate_exclusion(self):
        h, r = self.fixture()
        other = r.copy()
        other[2] = 'NT other'
        overseas = r.copy()
        overseas[0], overseas[2] = 'Overseas', 'Overseas'
        rows, excluded, _ = clean(h, [r, other, overseas])
        self.assertEqual((len(rows), len(excluded)), (1, 2))
        self.assertEqual([x['reason'] for x in excluded],
                         ['state_other_aggregate', 'overseas_aggregate'])
        self.assertEqual(excluded[0]['individual_count'], 100)

    def test_unknown_label_or_state_fails(self):
        h, r = self.fixture()
        for column, value in [(2, 'Unknown'), (2, None), (0, None), (0, 'Overseas'), (0, 'XX')]:
            bad = r.copy()
            bad[column] = value
            with self.assertRaises(ValueError):
                clean(h, [bad])

    def test_blank_cell_is_missing_and_audited(self):
        h, r = self.fixture()
        r[6] = None  # salary recipient count
        rows, _, issues = clean(h, [r])
        self.assertIsNone(rows[0]['ato_salary_or_wages_per_recipient'])
        self.assertTrue(rows[0]['ato_any_feature_missing'] and rows[0]['ato_quality_issue'])
        self.assertIn('blank', {x['reason'] for x in issues})

    def test_label_count_above_individuals_is_flagged_not_dropped(self):
        h, r = self.fixture()
        r[6] = 120
        rows, _, issues = clean(h, [r])
        self.assertEqual(rows[0]['ato_salary_recipient_count'], 120)
        self.assertIsNone(rows[0]['ato_salary_recipient_share'])
        self.assertIn('label_count_above_individual_count_retained', {x['reason'] for x in issues})

    def test_small_denominator_and_sa4_placeholder(self):
        h, r = self.fixture()
        rows, _, _ = clean(h, [r])  # smallest denominator used is the salary count, 50
        self.assertEqual(rows[0]['ato_min_derived_denominator'], 50)
        self.assertTrue(rows[0]['ato_small_denominator'])
        self.assertFalse(rows[0]['ato_sa4_is_state_other'])
        big = [v * SMALL_DENOMINATOR if isinstance(v, int) and i > 2 else v for i, v in enumerate(r)]
        big[1] = 'NT other'
        rows, _, _ = clean(h, [big])
        self.assertFalse(rows[0]['ato_small_denominator'])
        self.assertTrue(rows[0]['ato_sa4_is_state_other'])

    def test_coverage_keeps_unmatched(self):
        h, r = self.fixture()
        rows, _, _ = clean(h, [r])
        pairs = {('0800', 'NT'): 2, ('0800', 'SA'): 1, ('9999', 'QLD'): 2, (None, 'NT'): 1}
        report, exceptions, missing, states = coverage(pairs, rows, {'seifa': {'0800', '9999'}, 'census': {'9999'}})
        get = lambda scope, metric: next(x for x in report if (x['scope'], x['metric']) == (scope, metric))
        self.assertEqual(sum(x['rows'] for x in report[:3]), 6)
        self.assertEqual(get('consumer_rows', 'matched')['rate'], .5)
        self.assertEqual(get('distinct_valid_postcodes', 'matched')['rate'], .5)
        self.assertEqual(get('consumer_rows', 'postcode_in_ato_and_seifa')['rows'], 3)
        self.assertEqual(get('consumer_rows', 'postcode_in_all_sources')['rows'], 0)
        self.assertEqual(sum(x['consumer_count'] for x in exceptions), 3)
        self.assertEqual(states, [{'consumer_state': 'SA', 'ato_state': 'NT', 'consumer_rows': 1}])
        self.assertTrue(all(x['available_consumer_rows'] == 3 for x in missing))
        self.assertTrue(po_box_lvr_range('1109') and not po_box_lvr_range('3000'))

    def test_consumer_reader_csv_and_zip(self):
        text = 'name|address|state|postcode|gender|consumer_id\nA B|1 St|NT|862|Female|1\nC D|2 St|VIC|3000|Male|2\n'
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / 'tbl_consumer.csv'
            plain.write_text(text, encoding='utf-8')
            packed = Path(tmp) / 'part1.zip'
            with zipfile.ZipFile(packed, 'w') as z:
                z.writestr('tables/tbl_consumer.csv', text)
            for path in (plain, packed):
                self.assertEqual(dict(consumer_counts(path)), {('0862', 'NT'): 1, ('3000', 'VIC'): 1})

    def test_table_6a_reconciliation(self):
        h, r = self.fixture()
        half = [v // 2 if isinstance(v, int) and i > 2 else v for i, v in enumerate(r)]
        table_b = [('title',), tuple(h), tuple(r)]
        table_a = [('title',), ('Taxable status', *h), ('Taxable', *half), ('Non Taxable', *half)]
        self.assertEqual(sum(x['records_beyond_tolerance'] for x in reconcile_table_6a(table_a, table_b)), 0)
        table_a[2] = ('Taxable', *half[:3], half[3] + 7, *half[4:])
        self.assertEqual(reconcile_table_6a(table_a, table_b)[0]['records_beyond_tolerance'], 1)


class OutputTests(unittest.TestCase):
    def test_parquet_types_and_nulls(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        h, r = CleaningTests().fixture()
        r[6] = None
        rows, _, _ = clean(h, [r])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clean.parquet'
            write_parquet(path, rows)
            t = pq.read_table(path)
            self.assertEqual(t.schema.field('postcode').type, pa.string())
            self.assertEqual(t.schema.field('ato_individual_count').type, pa.int64())
            self.assertEqual(t.schema.field('ato_quality_issue').type, pa.bool_())
            self.assertEqual(t.to_pylist()[0]['postcode'], '0800')
            self.assertIsNone(t.to_pylist()[0]['ato_salary_recipient_count'])

    def test_missing_zero_record_fails_reconciliation(self):
        h, r = CleaningTests().fixture()
        zero = [v * 0 if isinstance(v, int) and i > 2 else v for i, v in enumerate(r)]
        report = reconcile_table_6a([('title',), ('Taxable status', *h)], [('title',), tuple(h), tuple(zero)])
        with self.assertRaises(ValueError):
            assert_reconciliation(report)

    def test_mismatched_aggregate_state_rejected(self):
        h, r = CleaningTests().fixture()
        r[2] = 'NSW other'
        with self.assertRaises(ValueError):
            clean(h, [r])


RAW = Path(__file__).resolve().parents[2] / 'tables/external/ato_2021_22' / RAW_NAME


@unittest.skipUnless(RAW.exists(), 'official workbook not downloaded')
class OfficialWorkbookRegression(unittest.TestCase):
    def test_known_shape_of_2021_22_table_6b(self):
        import openpyxl
        book = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
        source = list(book['Table 6B'].values)
        book.close()
        rows, excluded, issues = clean(source[1], source[2:])
        self.assertEqual((len(rows), len(excluded), len(issues)), (2630, 9, 0))
        self.assertEqual(rows[0]['postcode'], '0800')
        # Smallest published postcode has 51 individuals: smaller ones sit in the "<STATE> other" rows.
        self.assertEqual(min(x['ato_individual_count'] for x in rows), 51)
        self.assertEqual(sum(x['individual_count'] for x in excluded), 135153)
        self.assertEqual(sum(x['ato_sa4_is_state_other'] for x in rows), 69)


if __name__ == '__main__':
    unittest.main()
