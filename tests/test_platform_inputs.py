import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def module():
    import importlib
    spec = importlib.util.find_spec('platform_inputs')
    assert spec is not None, 'platform input adapter required'
    return importlib.import_module('platform_inputs')


def macro(values=(70, -30, 0, 20)):
    return pd.DataFrame({'symbol': ['EN0002967'] * 4,
                         'period_date': ['20200102','20200103','20200106','20200107'],
                         'data_value': values})


def bars():
    return pd.DataFrame({'symbol':['SC2003.INE'] * 4,
                         'date':['20191230','20191231','20200102','20200103'],
                         'day_session_open':[480,481,485,490],
                         'close':[482,484,488,492],
                         'limit_up':[530]*4,'limit_down':[430]*4})


def test_macro_negative_zero_and_assumed_delayed_clock():
    rows, audit = module().macro_events(macro(), 'EN0002967', 2)
    assert rows.external_return.tolist() == pytest.approx([-100/70,1,1])
    assert rows.available_at.iloc[0] == '2020-01-05T00:00:00+00:00'
    assert set(rows.available_at_basis) == {'assumed'}
    assert audit['input_rows'] == 4


def test_macro_missing_is_not_filled_and_large_gap_excluded():
    x = macro((70,None,72,73))
    x.loc[3,'period_date']='20200120'
    rows, audit = module().macro_events(x,'EN0002967',2)
    assert rows.empty
    assert audit['output_rows'] == 0


@pytest.mark.parametrize('damage',['duplicate','wrong_symbol','infinity','bad_date'])
def test_macro_rejects_bad_source(damage):
    x = macro()
    if damage == 'duplicate': x.loc[1,'period_date']=x.loc[0,'period_date']
    if damage == 'wrong_symbol': x.loc[1,'symbol']='WRONG'
    if damage == 'infinity': x['data_value']=x.data_value.astype(float); x.loc[1,'data_value']=float('inf')
    if damage == 'bad_date': x.loc[1,'period_date']='20200230'
    with pytest.raises(ValueError): module().macro_events(x,'EN0002967',2)


def test_domestic_uses_day_open_and_same_contract_lag():
    x=bars(); x['open']=1
    rows, audit=module().domestic_windows(x)
    assert rows.window_id.tolist()==['SC2003.INE:20200102','SC2003.INE:20200103']
    assert rows.asset_return.iloc[0] == pytest.approx(488/485-1)
    assert rows.local_lag_return.iloc[0] == pytest.approx(484/482-1)
    assert pd.Timestamp(rows.local_lag_available_at.iloc[0]) < pd.Timestamp(rows.decision_at.iloc[0])


def test_domestic_future_change_cannot_change_past_features():
    x=bars(); a,_=module().domestic_windows(x)
    x.loc[3,'close']=499
    b,_=module().domestic_windows(x)
    pd.testing.assert_series_equal(a.iloc[0],b.iloc[0])
    assert a.local_lag_return.tolist()==b.local_lag_return.tolist()


def test_domestic_limit_or_missing_day_open_excluded_without_fallback():
    x=bars(); x.loc[2,'day_session_open']=530; x.loc[3,'day_session_open']=float('nan')
    rows,audit=module().domestic_windows(x)
    assert rows.empty
    assert len(audit['excluded'])==4
    with pytest.raises(ValueError): module().domestic_windows(x.drop(columns='day_session_open'))


def test_fx_positive_direction_and_delay():
    x=pd.DataFrame({'date':['20200102','20200103'],'USD_CNY':[6.9,7.0]})
    rows,_=module().currency_events(x,2)
    assert rows.fx_return.iloc[0]==pytest.approx(7/6.9-1)
    assert rows.available_at.iloc[0]=='2020-01-05T00:00:00+00:00'
    x.loc[1,'USD_CNY']=0
    with pytest.raises(ValueError): module().currency_events(x,2)
