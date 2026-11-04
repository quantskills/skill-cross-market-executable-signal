"""Convert documented PandaData date-only data into explicitly assumed inputs.

No network, credentials, price filling, parameter selection or time certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd


def dates(series):
    text = series.astype(str)
    if not text.str.fullmatch(r'\d{8}').all():
        raise ValueError('Expected YYYYMMDD dates')
    return pd.to_datetime(text, format='%Y%m%d', errors='raise')


def required(frame, columns):
    if not set(columns) <= set(frame):
        raise ValueError('Missing required columns: ' + str(sorted(set(columns)-set(frame))))


def numeric(series):
    result = pd.to_numeric(series, errors='raise').astype(float)
    if np.isinf(result.to_numpy()).any():
        raise ValueError('Infinite source value')
    return result


def changes(frame, date_column, value_column, delay_days, prefix, value_name, floor=None):
    if isinstance(delay_days, bool) or not isinstance(delay_days, int) or not 2 <= delay_days <= 30:
        raise ValueError('Delay must be integer 2..30 days')
    required(frame, [date_column, value_column])
    x = frame.copy()
    x['_date'] = dates(x[date_column])
    if x['_date'].duplicated().any():
        raise ValueError('Duplicate observation date')
    x = x.sort_values('_date')
    value = numeric(x[value_column])
    if floor is None and (value.dropna() <= 0).any():
        raise ValueError('Currency must be positive')
    previous = value.shift(1)
    denominator = previous if floor is None else previous.abs().clip(lower=floor)
    ret = (value - previous) / denominator
    gap = x['_date'].diff().dt.days
    ok = ret.notna() & gap.between(1, 7)
    rows = []
    for i in x.index[ok]:
        day = x.loc[i, '_date']
        rows.append({prefix+'_id': prefix+':'+day.strftime('%Y%m%d'),
                     'available_at': (day.tz_localize('UTC')+pd.Timedelta(days=delay_days)).isoformat(),
                     value_name: float(ret.loc[i]), 'available_at_basis': 'assumed'})
    columns = [prefix+'_id','available_at',value_name,'available_at_basis']
    audit = {'input_rows':len(x),'output_rows':len(rows),'excluded_dates':
             x.loc[~ok,'_date'].dt.strftime('%Y%m%d').tolist(),
             'availability_delay_days':delay_days,'time_source_independently_verified':False}
    return pd.DataFrame(rows, columns=columns), audit


def macro_events(frame, symbol, delay_days=2):
    required(frame, ['symbol','period_date','data_value'])
    if frame.empty or not frame.symbol.astype(str).eq(symbol).all():
        raise ValueError('Empty or mixed macro symbol')
    out,audit = changes(frame,'period_date','data_value',delay_days,'event','external_return',floor=20.0)
    out['event_id'] = symbol+':'+out.event_id
    audit['symbol']=symbol
    audit['feature']='price_difference_over_max_abs_previous_20_usd_not_investment_return'
    return out,audit


def currency_events(frame, delay_days=2):
    return changes(frame,'date','USD_CNY',delay_days,'fx','fx_return')


def domestic_windows(frame):
    required(frame,['date','symbol','day_session_open','close','limit_up','limit_down'])
    x=frame.copy()
    x['_date']=dates(x.date)
    if x.duplicated(['symbol','_date']).any():
        raise ValueError('Duplicate contract/date')
    if not x.symbol.astype(str).str.fullmatch(r'SC\d{4}\.INE').all():
        raise ValueError('Expected actual SC delivery contracts')
    for col in ['day_session_open','close','limit_up','limit_down']:
        x[col]=numeric(x[col])
    x=x.sort_values(['symbol','_date'])
    rows,excluded=[],[]
    for symbol,g in x.groupby('symbol',sort=True):
        delivery = pd.Period('20'+symbol[2:4]+'-'+symbol[4:6],freq='M')
        prev_close=g.close.shift(1); old_close=g.close.shift(2)
        prev_day=g['_date'].shift(1)
        for i,r in g.iterrows():
            day=r['_date']; reason=None
            if delivery != day.to_period('M')+2:
                reason='not_frozen_delivery_month'
            elif not np.isfinite([r.day_session_open,r.close,r.limit_up,r.limit_down]).all() or min(r.day_session_open,r.close,r.limit_up,r.limit_down)<=0:
                reason='invalid_or_missing_price'
            elif r.limit_up <= r.limit_down or not r.limit_down < r.day_session_open < r.limit_up:
                reason='entry_at_or_outside_price_limit'
            elif not np.isfinite([prev_close.loc[i],old_close.loc[i]]).all() or min(prev_close.loc[i],old_close.loc[i])<=0:
                reason='missing_same_contract_lag'
            elif (day-prev_day.loc[i]).days > 10:
                reason='stale_domestic_lag'
            if reason:
                excluded.append({'date':day.strftime('%Y%m%d'),'symbol':symbol,'reason':reason})
                continue
            stamp=day.tz_localize('Asia/Shanghai')
            rows.append({'window_id':symbol+':'+day.strftime('%Y%m%d'),
                         'decision_at':(stamp+pd.Timedelta(hours=8,minutes=55)).isoformat(),
                         'entry_at':(stamp+pd.Timedelta(hours=9)).isoformat(),
                         'exit_at':(stamp+pd.Timedelta(hours=15)).isoformat(),
                         'asset_return':float(r.close/r.day_session_open-1),
                         'local_lag_return':float(prev_close.loc[i]/old_close.loc[i]-1),
                         'local_lag_available_at':(prev_day.loc[i].tz_localize('Asia/Shanghai')+pd.Timedelta(hours=18)).isoformat(),
                         'available_at_basis':'assumed'})
    columns=['window_id','decision_at','entry_at','exit_at','asset_return','local_lag_return',
             'local_lag_available_at','available_at_basis']
    out=pd.DataFrame(rows,columns=columns).sort_values('decision_at').reset_index(drop=True)
    if out.decision_at.duplicated().any():
        raise ValueError('Ambiguous domestic decision date')
    return out,{'input_rows':len(x),'output_rows':len(out),'excluded':excluded,
                'executable':False,'calendar_independently_verified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['bars','macro','currency','symbol','output']:
        p.add_argument('--'+n,required=True)
    p.add_argument('--delay-days',type=int,default=2)
    a=p.parse_args(); out=Path(a.output)
    if out.exists(): raise ValueError('Refuse to overwrite existing inputs')
    bars=pd.read_csv(a.bars,dtype={'date':str,'symbol':str})
    macro=pd.read_csv(a.macro,dtype={'period_date':str,'symbol':str})
    currency=pd.read_csv(a.currency,dtype={'date':str})
    d,da=domestic_windows(bars); e,ea=macro_events(macro,a.symbol,a.delay_days)
    f,fa=currency_events(currency,a.delay_days)
    audit={'domestic':da,'external':ea,'fx':fa,'input_sha256':{
        name:hashlib.sha256(Path(getattr(a,name)).read_bytes()).hexdigest()
        for name in ['bars','macro','currency']}}
    out.mkdir(parents=True,exist_ok=False)
    for name,table in [('domestic',d),('events',e),('fx',f)]: table.to_csv(out/(name+'.csv'),index=False)
    (out/'input-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'output':str(out),'windows':len(d),'events':len(e),'fx':len(f),'executable':False}))


if __name__=='__main__': main()
