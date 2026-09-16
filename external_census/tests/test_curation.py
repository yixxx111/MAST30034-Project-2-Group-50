from pathlib import Path
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
import duckdb
from external_census.src.curation import (FIELDS,DataQualityError,clean_table,normalise_postcode,
    build_features,enrich_rows,consumer_coverage,run_pipeline,sha256)
from external_census.src.data_quality import ratio
from external_census.src.enrich_transactions import enrich_transactions


def make_zip(path, transform=None):
    values = {'Tot_P_P':'100','Age_20_24_yr_P':'10','Age_25_34_yr_P':'20','Age_35_44_yr_P':'15',
              'Median_age_persons':'40','Median_tot_hhd_inc_weekly':'1200',
              'Average_household_size':'2.5','CF_no_children_F':'20','CF_Total_F':'30','OPF_Total_F':'5',
              'Other_family_F':'5','Total_F':'60','P_Tot_Emp_Tot':'60','P_Tot_Unemp_Tot':'10',
              'P_Tot_LF_Tot':'70','P_Not_in_LF_Tot':'8','P_LFS_NS_Tot':'2','P_Tot_Tot':'80',
              'Tot_FHs_Tot':'60','Tot_Lone_P_H':'30','Tot_Group_H':'10','Tot_Tot':'100',
              'HI_3000_3499_Tot':'12','HI_3500_3999_Tot':'8','HI_4000_more_Tot':'10',
              'P_15_yrs_over_P':'80','Percent_Unem_loyment_P':'14.3',
              'Percnt_LabForc_prticipation_P':'87.5','non_sch_qual_Bchelr_Degree_P':'20'}
    with zipfile.ZipFile(path,'w') as z:
        for table, fields in FIELDS.items():
            rows = [dict(POA_CODE_2021=k,**{f[0]:values[f[0]] for f in fields}) for k in ['POA0800','POA3000','POA9494','POA9797']]
            d = pd.DataFrame(rows)
            if transform: d = transform(table,d)
            z.writestr('data/'+f'2021Census_{table}_AUST_POA.csv',d.to_csv(index=False))
    return path


@pytest.fixture
def source(tmp_path): return make_zip(tmp_path/'source.zip')


def test_postcode_normalisation():
    s = normalise_postcode(pd.Series([' 800 ','0800','3000','3000.0','POA3000','12345','',None,'１２３４']))
    assert s.iloc[:3].tolist()==['0800','0800','3000']
    assert s.iloc[3:].isna().all()


def test_realistic_formulas(source):
    d,reports,_ = build_features(source)
    r = d.iloc[0]
    assert d.postcode.tolist()==['0800','3000']
    assert r.census_couple_with_children_families==30
    assert r.census_couple_family_share==pytest.approx(50/60)
    assert r.census_single_parent_family_share==pytest.approx(5/60)
    assert r.census_employment_to_population_ratio==.75
    assert r.census_unemployment_rate==pytest.approx(10/70)
    assert r.census_lone_person_household_share==.3
    assert r.census_age_20_44_count == 45
    assert r.census_age_20_44_share == .45
    assert r.census_households_weekly_income_3000_plus_count == 30
    assert r.census_households_weekly_income_3000_plus_share == .3
    assert r.census_bachelor_degree_share == .25
    assert r.census_unemployment_rate_published_pct == 14.3
    assert reports['source_audit'].raw_rows.tolist()==[4]*7
    assert len(reports['excluded_poa_records'])==14
    comparison = reports['published_rate_comparison']
    assert len(comparison) == 4 and comparison.comparison_status.eq('both_available').all()


@pytest.mark.parametrize('kind,value',[('count','-1'),('count','1.5'),('count','oops'),('count','inf')])
def test_invalid_population_becomes_null(kind,value):
    d,e,issues,_ = clean_table(pd.DataFrame({'POA_CODE_2021':['POA3000'],'Tot_P_P':[value],
                                             'Age_20_24_yr_P':['1'],'Age_25_34_yr_P':['2'],'Age_35_44_yr_P':['3']}),'G01')
    assert len(d)==1 and d.census_population.isna().all() and len(issues)==1


def test_zero_population_retained():
    d,_,issues,_ = clean_table(pd.DataFrame({'POA_CODE_2021':['POA3000'],'Tot_P_P':['0'],
                                             'Age_20_24_yr_P':['0'],'Age_25_34_yr_P':['0'],'Age_35_44_yr_P':['0']}),'G01')
    assert d.census_population.iloc[0]==0 and not issues


def test_missing_schema():
    with pytest.raises(DataQualityError,match='missing required'):
        clean_table(pd.DataFrame({'POA_CODE_2021':['POA3000']}),'G01')


@pytest.mark.parametrize('keys',[['POA3000','POA3000'],[' poa3000 ','POA3000']])
def test_duplicate_fail(keys):
    with pytest.raises(DataQualityError,match='duplicate POA'):
        clean_table(pd.DataFrame({'POA_CODE_2021':keys,'Tot_P_P':['10','20'],
                                  'Age_20_24_yr_P':['1','1'],'Age_25_34_yr_P':['2','2'],'Age_35_44_yr_P':['3','3']}),'G01')


def test_invalid_and_special_key_quarantine():
    frame = pd.DataFrame({'POA_CODE_2021':['POA3000','POA9494','POA9797','POA800','',None],'Tot_P_P':['10']*6,
                          'Age_20_24_yr_P':['1']*6,'Age_25_34_yr_P':['2']*6,'Age_35_44_yr_P':['3']*6})
    d,e,_,audit = clean_table(frame,'G01')
    assert len(d)==1 and len(e)==5 and audit['invalid_key_rows']==3 and audit['special_geography_rows']==2


def test_zero_medians_policy():
    frame = pd.DataFrame({'POA_CODE_2021':['POA3000'],'Median_age_persons':['0'],
                          'Median_tot_hhd_inc_weekly':['0'],'Average_household_size':['0']})
    d,_,i,_ = clean_table(frame,'G02')
    assert pd.isna(d.census_median_age.iloc[0]) and pd.isna(d.census_avg_household_size.iloc[0])
    assert d.census_median_household_income_weekly.iloc[0]==0 and len(i)==2


def test_ratio_zero_missing_and_perturbation():
    value,reason = ratio(pd.Series([2.,0.,np.nan,11.,0.]),pd.Series([0.,0.,5.,10.,10.]))
    assert value.iloc[:4].isna().all() and value.iloc[4]==0
    assert reason.iloc[0]=='zero_denominator' and reason.iloc[2]=='missing_input'
    assert reason.iloc[3]=='outside_0_1_possible_perturbation'


def test_coverage_mismatch_stops(tmp_path):
    p = make_zip(tmp_path/'bad.zip',lambda t,d:d[d.POA_CODE_2021.ne('POA0800')] if t=='G42' else d)
    with pytest.raises(DataQualityError,match='coverage differs'): build_features(p)


def test_missing_table_stops(tmp_path):
    p = tmp_path/'bad.zip'
    with zipfile.ZipFile(p,'w') as z: z.writestr('unrelated.csv','a\n1')
    with pytest.raises(DataQualityError,match='exactly one'): build_features(p)


def test_join_preserves_rows_and_causes(source):
    features,_,_ = build_features(source)
    left = pd.DataFrame({'consumer_postcode':['0800','800','3000','9999','9494',None],'order_id':list('ABCDEF')})
    out = enrich_rows(left,features)
    assert len(out)==len(left) and out.order_id.tolist()==left.order_id.tolist()
    assert out.census_match_status.tolist()==['matched']*3+['postcode_not_in_census','special_geography','missing_or_invalid_postcode']
    assert out.loc[~out.census_matched,'census_population'].isna().all()


def test_join_duplicate_dimension_stops(source):
    d,_,_ = build_features(source)
    with pytest.raises(DataQualityError,match='unique'):
        enrich_rows(pd.DataFrame({'consumer_postcode':['0800']}),pd.concat([d,d]))


def test_double_enrichment_stops(source):
    d,_,_ = build_features(source)
    joined = enrich_rows(pd.DataFrame({'consumer_postcode':['0800']}),d)
    with pytest.raises(DataQualityError,match='already contains'): enrich_rows(joined,d)


def test_pipe_consumer_and_missing_classification(source,tmp_path):
    d,_,_ = build_features(source)
    d.loc[d.postcode.eq('0800'),'census_median_age'] = np.nan
    p = tmp_path/'consumer.csv'
    p.write_text('name|postcode|consumer_id\nPrivate Name|800|1\nAnother|9999|2\nPerson||3\n')
    coverage,missing,exceptions,_ = consumer_coverage(p,d)
    assert coverage.query("scope=='consumer_rows' and metric=='matched'").rows.iloc[0]==1
    r = missing.set_index('field').loc['census_median_age']
    assert r.all_consumer_missing==3 and r.matched_consumer_missing==1 and r.unmatched_consumer_rows==2
    assert 'name' not in exceptions and 'consumer_id' not in exceptions


def test_run_reproducible_immutable_and_parquet(source,tmp_path):
    before = sha256(source)
    output = tmp_path/'result'
    run_pipeline(source,output)
    csv_hash = sha256(output/'census_clean.csv')
    run_pipeline(source,output)
    assert sha256(source)==before and sha256(output/'census_clean.csv')==csv_hash
    with duckdb.connect() as con:
        d = con.read_parquet(str(output/'census_clean.parquet')).df()
    assert d.postcode.tolist()==['0800','3000']
    assert d.census_source_year.tolist()==[2021,2021]


def test_output_cannot_replace_unrelated_files(source,tmp_path):
    output = tmp_path/'owned_by_someone_else';output.mkdir()
    (output/'keep.txt').write_text('keep')
    with pytest.raises(DataQualityError,match='dedicated'):run_pipeline(source,output)
    assert (output/'keep.txt').read_text()=='keep'


def test_output_cannot_contain_source(source,tmp_path):
    with pytest.raises(DataQualityError,match='input'):run_pipeline(source,tmp_path)


def test_parquet_integration_preserves_core(source,tmp_path):
    run_pipeline(source,tmp_path/'census')
    rows = pd.DataFrame({'order_id':['a','b','c','d'],'consumer_postcode':['800','3000','9999',None],
                         'dollar_value':[10.,20.,30.,40.],'merchant_master_matched':[True,False,True,True]})
    input_path = tmp_path/'base.parquet'
    with duckdb.connect() as con:
        con.register('base',rows)
        con.execute(f"COPY base TO '{input_path}' (FORMAT PARQUET)")
    original_hash = sha256(input_path)
    result = enrich_transactions(input_path,tmp_path/'census/census_clean.parquet',tmp_path/'enriched')
    with duckdb.connect() as con: saved = con.read_parquet(str(tmp_path/'enriched/curated_transactions_with_census.parquet')).df()
    saved = saved.sort_values('order_id')
    assert result['output_rows']==4 and saved.dollar_value.tolist()==[10,20,30,40]
    assert saved.merchant_master_matched.tolist()==[True,False,True,True]
    assert saved.census_matched.tolist()==[True,True,False,False]
    assert sha256(input_path)==original_hash
    cov = pd.read_csv(tmp_path/'enriched/transaction_join_coverage.csv').set_index('census_match_status')
    assert cov.loc['matched','transaction_rate']==.5
    assert cov.loc['matched','dollar_value_rate']==.3


def test_partitioned_curated_output(source,tmp_path):
    run_pipeline(source,tmp_path/'census')
    base = tmp_path/'curated_transactions'
    with duckdb.connect() as con:
        for year,month,order_id,postcode in [(2021,1,'one','0800'),(2022,2,'two','9999')]:
            partition = base/f'order_year={year}'/f'order_month={month}'
            partition.mkdir(parents=True)
            con.execute(f"COPY (SELECT '{order_id}' AS order_id, '{postcode}' AS consumer_postcode, 10.0 AS dollar_value) TO '{partition}/data.parquet' (FORMAT PARQUET)")
    result = enrich_transactions(base,tmp_path/'census/census_clean.parquet',tmp_path/'enriched')
    assert result['input_partition_count']==2 and result['output_rows']==2
    with duckdb.connect() as con:
        d=con.read_parquet(str(tmp_path/'enriched/curated_transactions_with_census.parquet')).df().sort_values('order_id')
    assert d.order_year.tolist()==[2021,2022] and d.order_month.tolist()==[1,2]
    assert d.census_matched.tolist()==[True,False]


def test_no_consumer_input_not_misreported(source,tmp_path):
    output = tmp_path/'census'
    consumer = tmp_path/'consumer.csv';consumer.write_text('postcode\n0800\n9999\n')
    run_pipeline(source,output,consumer)
    assert (output/'consumer_join_coverage.csv').exists()
    result = run_pipeline(source,output)
    assert result['consumer_join_status']=='not_run_no_consumer_input'
    assert not (output/'consumer_join_coverage.csv').exists()
