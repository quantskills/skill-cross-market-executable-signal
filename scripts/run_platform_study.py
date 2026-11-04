"""Run the frozen PandaData daily study locally or from QuantStudio.

Offline only. Dates remain explicitly assumed; this is not a trading engine.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from analyze import analyze
from platform_inputs import macro_events, currency_events, domestic_windows
from summarize_study import summarize, markdown


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def variants(cfg):
    delay=cfg['availability_delay_calendar_days']; ridge=cfg['ridge']
    return [('base',delay,ridge)]+[
        (f'delay_plus_{n}',delay+n,ridge) for n in cfg['extra_publication_delay_days']]+[
        (f'ridge_{r:g}',delay,r) for r in cfg['ridge_sensitivity'] if r != ridge]


def load_years(folder, stem, years, date_col):
    frames=[]; hashes={}
    for year in years:
        path=folder/f'{stem}_{year}.csv'
        x=pd.read_csv(path,dtype={date_col:str,'symbol':str})
        if x.empty or len(x) in (10000,50000,100000):
            raise ValueError(f'Empty or possibly capped data: {path.name}')
        if not x[date_col].str.startswith(str(year)).all():
            raise ValueError(f'Unexpected date outside requested year: {path.name}')
        frames.append(x); hashes[path.name]=digest(path)
    return pd.concat(frames,ignore_index=True),hashes


def run(study, raw, output):
    study,raw,output=map(Path,(study,raw,output))
    if output.exists(): raise ValueError('Output already exists; refuse overwrite')
    freeze=json.loads((study/'scope-freeze.json').read_text(encoding='utf-8'))
    for name in ['config.json','PREREGISTRATION.md']:
        matched=[x for x in freeze['files'] if x['name']==name]
        if len(matched)!=1 or digest(study/name)!=matched[0]['sha256']:
            raise ValueError('Frozen scope changed: '+name)
    cfg=json.loads((study/'config.json').read_text(encoding='utf-8'))
    bars,bh=load_years(raw/'wti_sc','sc_daily',range(2019,2026),'date')
    currency,fh=load_years(raw/'currency','currency',range(2018,2026),'date')
    domestic,da=domestic_windows(bars)
    sources={}; hashes={**bh,**fh}
    for symbol in [cfg['primary_external_symbol'],cfg['secondary_external_symbol']]:
        table,sh=load_years(raw/'wti_sc','wti_'+symbol,range(2018,2026),'period_date')
        sources[symbol]=table; hashes.update(sh)
    output.mkdir(parents=True,exist_ok=False)
    domestic.to_csv(output/'domestic.csv',index=False)
    write_json(output/'domestic-audit.json',da)
    write_json(output/'source-hashes.json',hashes)
    plan={'evaluation_years':[2023,2024,2025],'report_timezone':'Asia/Shanghai',
          **{k:cfg[k] for k in ['bootstrap_block_windows','bootstrap_repeats','seed']}}
    write_json(output/'reporting-plan.json',plan)
    combined=[]
    for symbol,table in sources.items():
        for name,delay,ridge in variants(cfg):
            dest=output/(symbol+'-'+name);dest.mkdir()
            events,ea=macro_events(table,symbol,delay)
            fx,fa=currency_events(currency,delay)
            effective={**cfg,'ridge':ridge,'availability_delay_calendar_days':delay,
                       'evaluated_external_symbol':symbol,'variant':name}
            write_json(dest/'config.json',effective)
            write_json(dest/'input-audit.json',{'external':ea,'fx':fa})
            events.to_csv(dest/'events.csv',index=False);fx.to_csv(dest/'fx.csv',index=False)
            report,signals=analyze(domestic,events,fx,effective)
            report['input_hashes']={'domestic':digest(output/'domestic.csv'),
                'events':digest(dest/'events.csv'),'fx':digest(dest/'fx.csv'),'config':digest(dest/'config.json')}
            write_json(dest/'report.json',report); signals.to_csv(dest/'signals.csv',index=False)
            summary=summarize(report,signals,plan)
            write_json(dest/'summary.json',summary)
            (dest/'REPORT.md').write_text(markdown(summary),encoding='utf-8')
            pd.DataFrame(summary['yearly']).to_csv(dest/'yearly.csv',index=False)
            pd.DataFrame(summary['paired_intervals']).to_csv(dest/'paired_intervals.csv',index=False)
            combined.extend({'symbol':symbol,'variant':name,**m} for m in report['metrics'])
            print(json.dumps({'symbol':symbol,'variant':name,'status':report['status'],
                              'coverage':report['alignment_coverage']},ensure_ascii=False),flush=True)
    pd.DataFrame(combined).to_csv(output/'comparison.csv',index=False)
    write_json(output/'run-evidence.json',{'scope_freeze_sha256':digest(study/'scope-freeze.json'),
        'code_hashes':{p.name:digest(p) for p in Path(__file__).parent.glob('*.py')},
        'output_hashes':{str(p.relative_to(output)).replace('\\','/'):digest(p)
                         for p in output.rglob('*') if p.is_file()},
        'executable':False,'platform_verified':False,
        'note':'Platform execution requires separate actual QuantStudio receipt.'})
    return combined


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['study','raw','output']:p.add_argument('--'+name,required=True)
    a=p.parse_args();run(a.study,a.raw,a.output)


if __name__=='__main__':main()
