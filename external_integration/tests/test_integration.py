import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from integrate import build_dimension,enrich,parquet_source,write_transaction_diagnostics


class IntegrationTests(unittest.TestCase):
    def fixture(self,root):
        paths={}
        for s,keys in [('census',['0800','3000']),('seifa',['0800','4000']),('ato',['0800','5000'])]:
            path=root/f'{s}.parquet';paths[s]=path
            year='ato_income_year' if s=='ato' else f'{s}_source_year'
            df=pd.DataFrame({'postcode':keys,year:['2021-22' if s=='ato' else 2021]*2,f'{s}_value':[None,2.]})
            if s!='ato':df['poa_code']=['POA'+k for k in keys]
            df.to_parquet(path,index=False)
        return paths

    def test_outer_union_and_prefixes(self):
        with tempfile.TemporaryDirectory() as t:
            d,_,_=build_dimension(self.fixture(Path(t)))
            self.assertEqual(set(d.postcode),{'0800','3000','4000','5000'})
            self.assertIn('census_poa_code',d)
            self.assertIn('seifa_poa_code',d)
            self.assertEqual(int(d.all_sources_matched.sum()),1)
            self.assertTrue(d.loc[d.postcode=='0800','census_value'].isna().all())

    def test_duplicate_year_null_rejected(self):
        for mode in ['duplicate','year','null']:
            with tempfile.TemporaryDirectory() as t:
                p=self.fixture(Path(t));d=pd.read_parquet(p['ato'])
                if mode=='duplicate':d.loc[1,'postcode']='800'
                elif mode=='year':d.loc[1,'ato_income_year']='2022-23'
                else:d.loc[1,'postcode']=None
                d.to_parquet(p['ato'],index=False)
                with self.assertRaises(ValueError):build_dimension(p)

    def test_transaction_row_amount_and_missingness(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);d,_,_=build_dimension(self.fixture(r));c=duckdb.connect()
            query="SELECT * FROM (VALUES ('a','800',100.0),('b','3000',200.0),('c','9999',50.0),('d',NULL,25.0)) t(order_id,consumer_postcode,dollar_value)"
            info=enrich(c,d,query,'order_id','consumer_postcode',r/'out.parquet',r,'transaction','dollar_value')
            self.assertEqual(info['input_rows'],4);self.assertEqual(info['amount_sum_before'],info['amount_sum_after'])
            o=pd.read_parquet(r/'out.parquet')
            self.assertEqual(o.loc[o.order_id=='a','external_postcode'].iloc[0],'0800')
            self.assertFalse(o.loc[o.order_id=='c','ato_matched'].iloc[0])
            self.assertTrue(o.loc[o.order_id=='a','census_matched'].iloc[0])
            miss=pd.read_csv(r/'transaction_feature_missingness.csv')
            self.assertEqual(int(miss.loc[miss.field=='census_value','matched_but_missing_rows'].iloc[0]),1)
            cov=pd.read_csv(r/'transaction_coverage.csv')
            self.assertAlmostEqual(cov.loc[cov.metric=='all_sources_matched','amount_coverage_rate'].iloc[0],100/375)
            with self.assertRaises(ValueError):enrich(c,d,'SELECT * FROM '+parquet_source(r/'out.parquet'),'order_id','consumer_postcode',r/'twice.parquet',r,'transaction','dollar_value')
            c.close()

    def test_transaction_diagnostics_by_state_and_match_status(self):
        # Regression guard for the review finding that transaction_coverage_by_state.csv
        # and transaction_amount_by_match_status.csv had no generation code in the actual
        # pipeline (they existed on disk from a one-off script). This exercises the real
        # write_transaction_diagnostics() helper that integrate.py's run() now calls.
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            d, _, _ = build_dimension(self.fixture(r))
            c = duckdb.connect()
            query = ("SELECT * FROM (VALUES "
                     "('a','800','NSW',100.0),('b','800','VIC',200.0),"
                     "('c','9999','NSW',50.0),('d','800','VIC',25.0)) "
                     "t(order_id,consumer_postcode,consumer_state,dollar_value)")
            enrich(c, d, query, 'order_id', 'consumer_postcode', r / 'out.parquet', r, 'transaction', 'dollar_value')
            written = write_transaction_diagnostics(c, r)
            self.assertEqual(set(written), {'transaction_coverage_by_state.csv', 'transaction_amount_by_match_status.csv'})
            by_state = pd.read_csv(r / 'transaction_coverage_by_state.csv').set_index('consumer_state')
            # NSW: 2 transactions (a matched, c unmatched -> postcode 9999 not in any source) = 50% matched
            self.assertAlmostEqual(by_state.loc['NSW', 'all_sources_matched_rate'], 0.5)
            # VIC: 2 transactions (b, d), both use postcode 800, matched all three sources
            self.assertAlmostEqual(by_state.loc['VIC', 'all_sources_matched_rate'], 1.0)
            by_match = pd.read_csv(r / 'transaction_amount_by_match_status.csv')
            self.assertEqual(set(by_match['all_sources_matched']), {True, False})
            c.close()

    def test_transaction_diagnostics_skipped_without_consumer_state(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            d, _, _ = build_dimension(self.fixture(r))
            c = duckdb.connect()
            query = "SELECT * FROM (VALUES ('a','800',100.0)) t(order_id,consumer_postcode,dollar_value)"
            enrich(c, d, query, 'order_id', 'consumer_postcode', r / 'out.parquet', r, 'transaction', 'dollar_value')
            written = write_transaction_diagnostics(c, r)
            self.assertEqual(written, [])
            self.assertFalse((r / 'transaction_coverage_by_state.csv').exists())
            c.close()

    def test_duplicate_order_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);d,_,_=build_dimension(self.fixture(r));c=duckdb.connect()
            with self.assertRaises(ValueError):enrich(c,d,"SELECT 'a' order_id, '0800' consumer_postcode FROM range(2)",'order_id','consumer_postcode',r/'bad.parquet',r,'transaction')
            c.close()

    def test_partitioned_input(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);d,_,_=build_dimension(self.fixture(r));folder=r/'input/order_year=2022';folder.mkdir(parents=True)
            pd.DataFrame({'order_id':['a'],'consumer_postcode':['0800'],'dollar_value':[1.25]}).to_parquet(folder/'part.parquet',index=False)
            c=duckdb.connect();enrich(c,d,'SELECT * FROM '+parquet_source(r/'input'),'order_id','consumer_postcode',r/'out.parquet',r,'transaction','dollar_value')
            self.assertEqual(pd.read_parquet(r/'out.parquet').order_year.iloc[0],2022);c.close()


if __name__=='__main__':unittest.main()
