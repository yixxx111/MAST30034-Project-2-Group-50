"""Quality utilities: preserve source evidence, distinguish missingness causes."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ratio(numerator: pd.Series, denominator: pd.Series):
    """Never divide by zero or silently clip perturbed counts into a valid ratio."""
    value = numerator / denominator.where(denominator.gt(0))
    reasons = pd.Series('', index=numerator.index, dtype='string')
    reasons.loc[numerator.isna() | denominator.isna()] = 'missing_input'
    reasons.loc[denominator.eq(0)] = 'zero_denominator'
    outside = value.notna() & (~np.isfinite(value) | value.lt(0) | value.gt(1))
    reasons.loc[outside] = 'outside_0_1_possible_perturbation'
    return value.mask(outside), reasons


def profile_features(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for name in frame.select_dtypes(include='number').columns:
        s = frame[name]
        records.append(dict(field=name, rows=len(s), missing_rows=int(s.isna().sum()),
                            missing_rate=float(s.isna().mean()) if len(s) else None,
                            zero_rows=int(s.eq(0).sum()), minimum=s.min(),
                            median=s.median(), p99=s.quantile(.99), maximum=s.max()))
    return pd.DataFrame(records)


def outlier_profile(frame: pd.DataFrame):
    """Distribution review only: no winsorisation or statistical outlier removal."""
    summaries, flags = [], []
    for field in ['census_population', 'census_median_age',
                  'census_median_household_income_weekly', 'census_avg_household_size']:
        s = frame[field]
        threshold = s.quantile(.99)
        mask = s.gt(threshold)
        summaries.append(dict(field=field, nonmissing_rows=int(s.notna().sum()),
                              p99=threshold, flagged_rows=int(mask.sum()), removed_rows=0,
                              action='retain; upper-tail review only'))
        for key, value in zip(frame.loc[mask,'postcode'], s[mask]):
            flags.append(dict(postcode=key, field=field, value=value, threshold=threshold))
    return pd.DataFrame(summaries), pd.DataFrame(flags, columns=['postcode','field','value','threshold'])
