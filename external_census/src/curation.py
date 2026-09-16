from __future__ import annotations
import csv
import hashlib
import io
import json
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from .data_quality import profile_features, ratio, outlier_profile


class DataQualityError(ValueError):
    pass


FIELDS = {
    'G01': [
        ('Tot_P_P','population','Total persons, usual residence','count'),
        ('Age_20_24_yr_P','age_20_24','Persons aged 20–24','count'),
        ('Age_25_34_yr_P','age_25_34','Persons aged 25–34','count'),
        ('Age_35_44_yr_P','age_35_44','Persons aged 35–44','count')],
    'G02': [
        ('Median_age_persons','median_age','Median age of persons','age'),
        ('Median_tot_hhd_inc_weekly','median_household_income_weekly','Median total household income','income'),
        ('Average_household_size','avg_household_size','Average usual residents per occupied private dwelling','size')],
    'G29': [
        ('CF_no_children_F','couple_no_children_families','Couple families without children','count'),
        ('CF_Total_F','couple_with_children_families','Couple families WITH children; excludes childless couples','count'),
        ('OPF_Total_F','single_parent_families','One-parent families','count'),
        ('Other_family_F','other_families','Other families','count'),
        ('Total_F','total_families','Total families in occupied private dwellings','count')],
    'G33': [
        ('Tot_Tot','households_all','All households used as the income-share denominator','count'),
        ('HI_3000_3499_Tot','households_weekly_income_3000_3499','Households with weekly income AUD 3,000–3,499','count'),
        ('HI_3500_3999_Tot','households_weekly_income_3500_3999','Households with weekly income AUD 3,500–3,999','count'),
        ('HI_4000_more_Tot','households_weekly_income_4000_plus','Households with weekly income AUD 4,000 or more','count')],
    'G43': [
        ('P_15_yrs_over_P','population_15plus_g43','Persons aged 15 years and over','count'),
        ('Percent_Unem_loyment_P','unemployment_rate_published_pct','ABS-published unemployment rate','percentage'),
        ('Percnt_LabForc_prticipation_P','labour_force_participation_rate_published_pct','ABS-published labour-force participation rate','percentage'),
        ('non_sch_qual_Bchelr_Degree_P','bachelor_degree_count','Persons with a bachelor degree','count')],
    'G46B': [
        ('P_Tot_Emp_Tot','employed_persons','Employed persons aged 15+','count'),
        ('P_Tot_Unemp_Tot','unemployed_persons','Unemployed persons aged 15+','count'),
        ('P_Tot_LF_Tot','labour_force','Persons aged 15+ in labour force','count'),
        ('P_Not_in_LF_Tot','not_in_labour_force','Persons aged 15+ outside labour force','count'),
        ('P_LFS_NS_Tot','labour_status_not_stated','Persons aged 15+ with labour force status not stated','count'),
        ('P_Tot_Tot','population_15plus','All persons aged 15+, including labour status not stated','count')],
    'G42': [
        ('Tot_FHs_Tot','family_households','Family households','count'),
        ('Tot_Lone_P_H','lone_person_households','Lone-person households','count'),
        ('Tot_Group_H','group_households','Group households','count'),
        ('Tot_Tot','total_households','Total classified occupied private dwellings','count')]
}
SPECIAL = {'9494': 'no_usual_address', '9797': 'migratory_offshore_shipping'}
RATIOS = {
    'employment_to_population_ratio': ('employed_persons','population_15plus'),
    'unemployment_rate': ('unemployed_persons','labour_force'),
    'labour_force_participation_rate': ('labour_force','population_15plus'),
    'couple_no_children_family_share': ('couple_no_children_families','total_families'),
    'couple_with_children_family_share': ('couple_with_children_families','total_families'),
    'couple_family_share': ('couple_families','total_families'),
    'single_parent_family_share': ('single_parent_families','total_families'),
    'family_household_share': ('family_households','total_households'),
    'lone_person_household_share': ('lone_person_households','total_households'),
    'group_household_share': ('group_households','total_households')
}
ISSUE_COLUMNS = ['source_table','poa_code','field','raw_value','reason','action']
ABS_POA_URL = 'https://www.abs.gov.au/census/find-census-data/datapacks/download/2021_GCP_POA_for_AUS_short-header.zip'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def download_abs_datapack(destination: str | Path) -> Path:
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    request = urllib.request.Request(ABS_POA_URL, headers={'User-Agent': 'MAST30034-project'})
    with urllib.request.urlopen(request, timeout=120) as response:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='abs-poa-', suffix='.zip', dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            shutil.copyfileobj(response, temporary)
    temporary_path.replace(destination)
    return destination


def normalise_postcode(values: pd.Series) -> pd.Series:
    s = values.astype('string').str.strip()
    return s.where(s.str.fullmatch(r'[0-9]{1,4}', na=False)).str.zfill(4)


def clean_table(frame: pd.DataFrame, table: str):
    required = ['POA_CODE_2021'] + [f[0] for f in FIELDS[table]]
    missing = set(required) - set(frame.columns)
    if missing: raise DataQualityError(f'{table}: missing required columns: {sorted(missing)}')
    raw = frame[required].copy()
    keys = raw['POA_CODE_2021'].astype('string').str.strip().str.upper()
    valid = keys.str.fullmatch(r'POA[0-9]{4}', na=False)
    special = keys.str[3:].isin(SPECIAL) & valid
    reasons = pd.Series('', index=raw.index, dtype='string')
    reasons.loc[~valid] = 'invalid_poa_code'
    reasons.loc[special] = keys[special].str[3:].map(SPECIAL)
    excluded = raw.loc[reasons.ne('')].copy()
    excluded.insert(0, 'source_table', table)
    excluded['reason'] = reasons[reasons.ne('')]
    d = raw.loc[valid & ~special].copy()
    d['POA_CODE_2021'] = keys[valid & ~special]
    duplicate = d['POA_CODE_2021'].duplicated(keep=False)
    if duplicate.any():
        examples = d.loc[duplicate, 'POA_CODE_2021'].unique()[:5].tolist()
        raise DataQualityError(f'{table}: duplicate POA keys {examples}; investigate instead of arbitrarily dropping rows')
    if d.empty: raise DataQualityError(f'{table}: no ordinary POAs remain')
    result = pd.DataFrame({'poa_code':d['POA_CODE_2021'], 'postcode':d['POA_CODE_2021'].str[3:]})
    issues = []
    source_missing_cells = source_non_numeric_cells = source_nonfinite_cells = 0
    for source, target, _, kind in FIELDS[table]:
        strings = d[source].astype('string').str.strip()
        values = pd.to_numeric(strings, errors='coerce').astype('float64')
        source_missing_cells += int(strings.isna().sum() + strings.eq('').sum())
        source_non_numeric_cells += int((values.isna() & strings.notna() & strings.ne('')).sum())
        source_nonfinite_cells += int((values.notna() & ~np.isfinite(values)).sum())
        why = pd.Series('', index=d.index, dtype='string')
        why.loc[values.isna()] = 'missing_or_non_numeric'
        why.loc[values.notna() & ~np.isfinite(values)] = 'non_finite'
        if kind == 'count':
            why.loc[values.lt(0) | (values.mod(1).ne(0) & values.notna())] = 'invalid_nonnegative_integer_count'
        elif kind == 'age':
            why.loc[values.lt(0) | values.gt(120)] = 'invalid_median_age'
            why.loc[values.eq(0)] = 'zero_median_age_not_interpreted'
        elif kind == 'size':
            why.loc[values.le(0)] = 'nonpositive_household_size_not_interpreted'
        elif kind == 'percentage':
            why.loc[values.lt(0) | values.gt(100)] = 'percentage_outside_0_to_100'
        # Zero/negative income is not automatically missing. Keep it with a review flag.
        for idx in why[why.ne('')].index:
            issues.append(dict(source_table=table, poa_code=result.at[idx,'poa_code'],
                               field='census_'+target, raw_value=str(strings.at[idx]),
                               reason=why.at[idx], action='clean cell set to null; POA retained'))
        result['census_'+target] = values.mask(why.ne(''))
    audit = dict(source_table=table, raw_rows=len(raw), accepted_rows=len(result),
                 excluded_rows=len(excluded), invalid_key_rows=int((~valid).sum()),
                 special_geography_rows=int(special.sum()), duplicate_keys=0,
                 numeric_cells_set_null=len(issues), source_missing_cells=source_missing_cells,
                 source_non_numeric_cells=source_non_numeric_cells, source_nonfinite_cells=source_nonfinite_cells)
    return result, excluded, issues, audit


def build_features(zip_path: str | Path):
    cleaned, excluded, issues, audits, members_used = [], [], [], [], []
    with zipfile.ZipFile(zip_path) as z:
        for table in FIELDS:
            filename = f'2021Census_{table}_AUST_POA.csv'
            members = [n for n in z.namelist() if Path(n).name==filename and not n.startswith('__MACOSX/')]
            if len(members)!=1: raise DataQualityError(f'Expected exactly one {filename}, found {len(members)}')
            data = z.read(members[0])
            try: header = next(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
            except StopIteration: raise DataQualityError(f'{table}: empty CSV')
            if len(header)!=len(set(header)): raise DataQualityError(f'{table}: duplicate CSV column names')
            frame = pd.read_csv(io.BytesIO(data),dtype='string',keep_default_na=False)
            c,e,i,a = clean_table(frame,table)
            cleaned.append(c); excluded.append(e); issues.extend(i); audits.append(a)
            members_used.append(dict(table=table,member=members[0],sha256=hashlib.sha256(data).hexdigest()))
    result = cleaned[0]
    for table, part in zip(list(FIELDS)[1:],cleaned[1:]):
        if set(result.postcode)!=set(part.postcode):
            raise DataQualityError(f'{table}: POA coverage differs from G01; inspect before joining')
        result = result.merge(part,on=['poa_code','postcode'],how='left',validate='one_to_one')
    result = result.sort_values('postcode').reset_index(drop=True)
    result['census_source_year'] = 2021
    result['census_couple_families'] = result.census_couple_no_children_families + result.census_couple_with_children_families
    result['census_age_20_44_count'] = result[
        ['census_age_20_24', 'census_age_25_34', 'census_age_35_44']
    ].sum(axis=1, min_count=3)
    result['census_households_weekly_income_3000_plus_count'] = result[
        ['census_households_weekly_income_3000_3499',
         'census_households_weekly_income_3500_3999',
         'census_households_weekly_income_4000_plus']
    ].sum(axis=1, min_count=3)
    ratio_issues = []
    for target,(n,d) in RATIOS.items():
        result['census_'+target], reasons = ratio(result['census_'+n],result['census_'+d])
        for idx in reasons[reasons.ne('')].index:
            ratio_issues.append(dict(postcode=result.at[idx,'postcode'],field='census_'+target,
                                     numerator=result.at[idx,'census_'+n],denominator=result.at[idx,'census_'+d],
                                     reason=reasons.at[idx]))
    additional_ratios = {
        'age_20_44_share': ('age_20_44_count', 'population'),
        'households_weekly_income_3000_plus_share': ('households_weekly_income_3000_plus_count', 'households_all'),
        'bachelor_degree_share': ('bachelor_degree_count', 'population_15plus_g43'),
    }
    for target, (n, d) in additional_ratios.items():
        result['census_' + target], reasons = ratio(result['census_' + n], result['census_' + d])
        for idx in reasons[reasons.ne('')].index:
            ratio_issues.append(dict(postcode=result.at[idx,'postcode'],field='census_'+target,
                                     numerator=result.at[idx,'census_'+n],denominator=result.at[idx,'census_'+d],
                                     reason=reasons.at[idx]))
    result['census_unemployment_rate_derived_pct'] = result['census_unemployment_rate'] * 100
    result['census_labour_force_participation_rate_derived_pct'] = result['census_labour_force_participation_rate'] * 100
    for label,field in [('population','population'),('labour_force','labour_force'),
                        ('families','total_families'),('households','total_households')]:
        result['census_small_'+label] = result['census_'+field].lt(30)
    result['census_nonpositive_household_income'] = result.census_median_household_income_weekly.le(0)
    result['census_zero_population'] = result.census_population.eq(0)
    result['census_any_feature_missing'] = result.select_dtypes(include='number').isna().any(axis=1)
    result['census_ratio_quality_issue'] = result.postcode.isin(
        [r['postcode'] for r in ratio_issues if r['reason']=='outside_0_1_possible_perturbation'])
    checks = []
    for name,components,total in [
        ('family_additivity',['couple_no_children_families','couple_with_children_families','single_parent_families','other_families'],'total_families'),
        ('household_additivity',['family_households','lone_person_households','group_households'],'total_households'),
        ('labour_force_additivity',['employed_persons','unemployed_persons'],'labour_force')]:
        delta = result[['census_'+x for x in components]].sum(axis=1,min_count=len(components))-result['census_'+total]
        for idx in delta[delta.notna() & delta.ne(0)].index:
            checks.append(dict(postcode=result.at[idx,'postcode'],check=name,difference=delta.at[idx],
                               action='retain counts; ABS perturbation can break additivity'))
    rate_comparisons = []
    for measure, derived, published in [
        ('unemployment_rate', 'census_unemployment_rate_derived_pct', 'census_unemployment_rate_published_pct'),
        ('labour_force_participation_rate', 'census_labour_force_participation_rate_derived_pct', 'census_labour_force_participation_rate_published_pct')]:
        for idx in result.index:
            derived_pct, published_pct = result.at[idx, derived], result.at[idx, published]
            if pd.notna(derived_pct) and pd.notna(published_pct):
                status, difference = 'both_available', abs(derived_pct - published_pct)
            elif pd.isna(derived_pct) and pd.isna(published_pct):
                status, difference = 'both_missing', np.nan
            elif pd.isna(derived_pct):
                status, difference = 'derived_missing', np.nan
            else:
                status, difference = 'published_missing', np.nan
            rate_comparisons.append(dict(
                postcode=result.at[idx, 'postcode'], measure=measure,
                derived_pct=derived_pct, published_pct=published_pct,
                absolute_difference_pp=difference, comparison_status=status,
                action='Use the ABS-published G43 percentage as the analysis feature; retain the derived G46B rate for QA.'))
    if np.isinf(result.select_dtypes(include='number').to_numpy(dtype=float)).any():
        raise DataQualityError('Nonfinite output')
    return result, {
        'source_audit':pd.DataFrame(audits),
        'excluded_poa_records':pd.concat(excluded,ignore_index=True),
        'numeric_issues':pd.DataFrame(issues,columns=ISSUE_COLUMNS),
        'ratio_issues':pd.DataFrame(ratio_issues,columns=['postcode','field','numerator','denominator','reason']),
        'published_rate_comparison':pd.DataFrame(rate_comparisons,columns=['postcode','measure','derived_pct','published_pct','absolute_difference_pp','comparison_status','action']),
        'additivity_checks':pd.DataFrame(checks,columns=['postcode','check','difference','action'])
    }, members_used


def enrich_rows(frame: pd.DataFrame, features: pd.DataFrame, postcode_column='consumer_postcode'):
    if postcode_column not in frame: raise DataQualityError(f'Missing {postcode_column}')
    if features.postcode.isna().any() or features.postcode.duplicated().any():
        raise DataQualityError('Census postcode dimension must be non-null and unique')
    if features.postcode.isin(SPECIAL).any(): raise DataQualityError('Special POAs must not enter the join dimension')
    fields = [c for c in features if c.startswith('census_')]
    reserved = fields+['census_postcode','census_poa_code','census_matched','census_match_status','_census_join']
    if set(reserved)&set(frame): raise DataQualityError('Input already contains Census or reserved join columns')
    left = frame.copy()
    left['census_postcode'] = normalise_postcode(left[postcode_column])
    right = features[['postcode','poa_code']+fields].rename(columns={'postcode':'census_postcode','poa_code':'census_poa_code'})
    joined = left.merge(right,on='census_postcode',how='left',validate='many_to_one',indicator='_census_join',sort=False)
    if len(joined)!=len(frame): raise DataQualityError('External join changed row count')
    joined['census_matched'] = joined.pop('_census_join').eq('both')
    joined['census_match_status'] = 'postcode_not_in_census'
    joined.loc[joined.census_matched,'census_match_status'] = 'matched'
    joined.loc[joined.census_postcode.isin(SPECIAL),'census_match_status'] = 'special_geography'
    joined.loc[joined.census_postcode.isna(),'census_match_status'] = 'missing_or_invalid_postcode'
    return joined


def consumer_coverage(path,features,postcode_column='postcode'):
    path = Path(path)
    with path.open(encoding='utf-8-sig') as f: header = f.readline()
    sep = '|' if '|' in header else ','
    consumers = pd.read_csv(path,sep=sep,usecols=[postcode_column],dtype='string',keep_default_na=False)
    joined = enrich_rows(consumers,features,postcode_column)
    total = len(joined)
    coverage = []
    for status in ['matched','postcode_not_in_census','special_geography','missing_or_invalid_postcode']:
        count = int(joined.census_match_status.eq(status).sum())
        coverage.append(dict(scope='consumer_rows',metric=status,rows=count,denominator=total,rate=count/total if total else None))
    valid_unique = joined.census_postcode.dropna().unique()
    matched_unique = joined.loc[joined.census_matched,'census_postcode'].nunique()
    coverage.append(dict(scope='distinct_valid_postcodes',metric='matched',rows=matched_unique,
                         denominator=len(valid_unique),rate=matched_unique/len(valid_unique) if len(valid_unique) else None))
    missing = []
    for col in [c for c in features if c.startswith('census_')]:
        missing.append(dict(field=col,all_consumer_missing=int(joined[col].isna().sum()),
                            matched_consumer_missing=int(joined.loc[joined.census_matched,col].isna().sum()),
                            unmatched_consumer_rows=int((~joined.census_matched).sum()),all_consumer_rows=total))
    counts = joined.groupby(['census_postcode','census_match_status'],dropna=False).size().reset_index(name='consumer_rows')
    return pd.DataFrame(coverage),pd.DataFrame(missing),counts[counts.census_match_status.ne('matched')].copy(),counts


def data_dictionary(features):
    records = [dict(field='postcode',source='POA_CODE_2021',definition='4-character POA-derived postcode; preserve leading zeros',unit='string'),
               dict(field='poa_code',source='POA_CODE_2021',definition='Normalised ABS POA key',unit='string')]
    for table,fields in FIELDS.items():
        for source,target,meaning,kind in fields:
            records.append(dict(field='census_'+target,source=f'2021Census_{table}_AUST_POA.csv :: {source}',definition=meaning,
                                unit={'count':'count','age':'years','income':'AUD/week','size':'persons/household','percentage':'percentage points'}[kind]))
    for target,(n,d) in RATIOS.items():
        records.append(dict(field='census_'+target,source=f'census_{n} / census_{d}',
                            definition='Null for missing input, zero denominator or ratio outside [0,1]; never clipped',unit='proportion 0-1'))
    known = {r['field'] for r in records}
    descriptions = {
        'census_couple_families':('Couple families with and without children summed','count'),
        'census_age_20_44_count':('Persons aged 20–44 summed from the three G01 age bands','count'),
        'census_households_weekly_income_3000_plus_count':('Households with weekly income AUD 3,000 or more summed from G33 bands','count'),
        'census_age_20_44_share':('Persons aged 20–44 divided by total population; null for undefined or perturbed ratios','proportion 0-1'),
        'census_households_weekly_income_3000_plus_share':('Households with weekly income AUD 3,000 or more divided by all G33 households','proportion 0-1'),
        'census_bachelor_degree_share':('Bachelor-degree count divided by G43 persons aged 15 years and over','proportion 0-1'),
        'census_unemployment_rate_derived_pct':('G46B unemployed persons divided by labour force, multiplied by 100; QA comparison only','percentage points'),
        'census_labour_force_participation_rate_derived_pct':('G46B labour force divided by population 15+, multiplied by 100; QA comparison only','percentage points'),
        'census_source_year':('Census reference year 2021','year'),
        'census_nonpositive_household_income':('Reported income is zero or negative; retained for review, not assumed missing','boolean'),
        'census_zero_population':('Reported population is zero','boolean'),
        'census_any_feature_missing':('At least one numeric clean feature is unavailable','boolean'),
        'census_ratio_quality_issue':('At least one derived ratio falls outside [0,1]','boolean')}
    for name in features:
        if name not in known:
            description,unit = descriptions.get(name,('Denominator below 30; review flag, not an exclusion rule','boolean'))
            records.append(dict(field=name,source='derived',definition=description,unit=unit))
    return pd.DataFrame(records)


def publish_directory(stage: Path, target: Path):
    backup = target.with_name(target.name+'.previous')
    if backup.exists(): raise DataQualityError(f'Recovery folder exists: {backup}; inspect before rerun')
    if target.exists(): target.rename(backup)
    try: stage.rename(target)
    except Exception:
        if backup.exists(): backup.rename(target)
        raise
    if backup.exists(): shutil.rmtree(backup)


def validate_output(target: Path,inputs,marker='census_metadata.json'):
    if any(p==target or target in p.parents for p in inputs):
        raise DataQualityError('Output must not contain or replace an input')
    if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not (target/marker).is_file())):
        raise DataQualityError('Output must be empty or a dedicated prior Census output directory')


def run_pipeline(zip_path,output_root,consumer_csv=None,consumer_postcode_column='postcode'):
    import duckdb
    zip_path,output_root = Path(zip_path).resolve(),Path(output_root).resolve()
    inputs = [zip_path]+([Path(consumer_csv).resolve()] if consumer_csv else [])
    validate_output(output_root,inputs)
    input_hash = sha256(zip_path)
    consumer_hash = sha256(Path(consumer_csv)) if consumer_csv else None
    features,reports,members = build_features(zip_path)
    reports['feature_quality_profile'] = profile_features(features)
    reports['outlier_summary'],reports['outlier_flags'] = outlier_profile(features)
    reports['data_dictionary'] = data_dictionary(features)
    join_status = 'not_run_no_consumer_input'
    if consumer_csv:
        cov,missing,exceptions,counts = consumer_coverage(consumer_csv,features,consumer_postcode_column)
        reports.update(consumer_join_coverage=cov,consumer_feature_missingness=missing,
                       consumer_postcode_exceptions=exceptions,consumer_postcode_counts=counts)
        join_status = 'completed_consumer_table_only'
    if sha256(zip_path)!=input_hash or (consumer_csv and sha256(Path(consumer_csv))!=consumer_hash):
        raise DataQualityError('Source changed during processing')
    metadata = dict(stage='external_census',schema_version=2,source_year=2021,
                    created_utc=datetime.now(timezone.utc).isoformat(),source_filename=zip_path.name,
                    source_url=ABS_POA_URL,source_bytes=zip_path.stat().st_size,
                    source_sha256=input_hash,source_members=members,clean_rows=len(features),clean_columns=len(features.columns),
                    consumer_join_status=join_status,transaction_join_status='not_run',
                    consumer_source_sha256=consumer_hash,
                    versions=dict(python=platform.python_version(),pandas=pd.__version__,numpy=np.__version__,duckdb=duckdb.__version__),
                    policies={'duplicates':'fail, investigate','invalid_numeric':'cell null; POA retained',
                              'special_geographies':'exclude 9494/9797; audit','nonpositive_income':'retain and flag; not assumed missing',
                              'zero_age_or_size':'null and audit; conservative analytical policy',
                              'ratios':'null when undefined or outside [0,1]; retain source counts',
                              'outliers':'retain; upper 1% review flags','small_denominator':'below 30 flag, never exclude',
                              'imputation':'none','join':'many-to-one left join; no row loss'},
                    limitations=['POA approximates postcodes; not all postal delivery codes represented',
                                 '2021 area context, not individual consumer attributes',
                                 'G29/G42 place of enumeration; G01/G46 usual residence',
                                 'G42 excludes visitors-only/other non-classifiable households',
                                 'Median household income excludes incompletely stated income and temporarily absent adult members',
                                 'ABS perturbation causes non-additivity and unstable small-area ratios',
                                 'Historical predictive models must respect source release availability'])
    output_root.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.census-',dir=output_root.parent))
    try:
        features.to_csv(stage/'census_clean.csv',index=False)
        with duckdb.connect() as con:
            con.register('census_frame',features)
            out = str(stage/'census_clean.parquet').replace("'","''")
            con.execute(f"COPY census_frame TO '{out}' (FORMAT PARQUET)")
        for name,frame in reports.items(): frame.to_csv(stage/(name+'.csv'),index=False)
        (stage/'census_metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        publish_directory(stage,output_root)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return metadata
