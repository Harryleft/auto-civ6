"""Offline evidence formatting only: no game access, actions or strategy."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import re

ROOT=Path(__file__).resolve().parents[1]
started={};turn=None;records=[]
for line in (ROOT/'evidence/live/debug.jsonl').read_text().splitlines():
    row=json.loads(line)
    if row.get('event')=='tool_started':started[row['sequence']]=row
    if row.get('event')!='tool_result':continue
    text='\n'.join(c.get('text','')for c in row['result'].get('content',[]))
    if row['tool']=='load_game_save'and not row['is_error']:
        match=re.search(r'\bturn (\d+)\.',text)
        if match:turn=int(match[1])
    begin=started[row['request']]
    item=dict(turn=turn,tool=row['tool'],args=begin['arguments'],sequence=row['sequence'],error=row['is_error'],
        bytes=len(text.encode()),rpc_seconds=(datetime.fromisoformat(row['timestamp'])-datetime.fromisoformat(begin['timestamp'])).total_seconds(),text=text)
    try:item['data']=json.loads(text)
    except json.JSONDecodeError:pass
    records.append(item)

def first(rows,tool,predicate=lambda r:True):
    return next((r for r in rows if r['tool']==tool and predicate(r)),None)

def metric(row):
    return {k:row[k]for k in ('tool','args','sequence','bytes','rpc_seconds','error')}if row else None

def coords(page):
    return re.findall(r'\((-?\d+),(-?\d+)\)',page['report'])

stages=[]
for t in (50,100,150,200):
    rows=[r for r in records if r['turn']==t]
    growth=first(rows,'get_city_growth',lambda r:r['args'].get('view')=='structured')
    if not growth:continue
    brief=first(rows,'get_city_growth',lambda r:'city_id'in r['args']and r['args'].get('view','brief')=='brief')
    evidence=first(rows,'get_city_growth',lambda r:'city_id'in r['args']and r['args'].get('view')=='evidence')
    global_rows=[r for r in rows if r['tool']=='get_global_settle_advisor'and isinstance(r.get('data'),dict)]
    full=next((r for r in global_rows if r['data']['limit']==200 and r['data']['scope']=='revealed map'),None)
    pages=sorted([r for r in global_rows if r['data']['limit']==20 and r['data']['scope']=='revealed map'],key=lambda r:r['data']['offset'])
    region=next((r for r in global_rows if isinstance(r['data']['scope'],dict)),None)
    recovered=[xy for p in pages for xy in coords(p['data'])]
    recover_ok=bool(full and pages and recovered==coords(full['data'])and all(len(coords(p['data']))==p['data']['shown']for p in pages))
    search=[r for r in rows if r['tool']=='get_public_report'and r['args'].get('section')=='map-search']
    locations=[loc for r in search for loc in (r.get('data',{}).get('data')or {}).get('locations',[])]
    fog=[loc for loc in locations if loc['visible_now']is False and loc['owner_id']!=0]
    fog_ok=all(all(loc[k]is None for k in ('owner_id','improvement_type','pillaged'))for loc in fog)
    tasks=first(rows,'get_builder_tasks');cities=first(rows,'get_cities')
    stage=dict(turn=t,growth=growth['data'],brief=metric(brief),evidence=metric(evidence),
        full=metric(full),first_page=metric(pages[0])if pages else None,region=metric(region),
        full_count=full['data']['total']if full else None,page_count=len(pages),pages_recover_full=recover_ok,
        region_count=region['data']['total']if region else None,
        all_page_bytes=sum(r['bytes']for r in pages),map_search=[dict(metric(r),result=r.get('data'))for r in search],
        fogged_foreign_dynamic_fields_withheld=fog_ok,fogged_foreign_count=len(fog),
        archaeology_not_builder=bool(tasks and not any(s in tasks['text']for s in ('ANTIQUITY_SITE','SHIPWRECK'))),
        city_sites=[]if not cities else [line for line in cities['text'].splitlines()if 'Archaeology sites'in line],
        resources=metric(first(rows,'get_empire_resources')))
    stage['turn_reconciliation']=[]
    for c in growth['data'].get('cities')or []:
        effective=c['derived']['growth_food_per_turn'];remaining=c['derived']['food_remaining']
        if effective is not None and effective>0 and remaining is not None and c['turns_until_growth']>=0:
            calculated=math.ceil(remaining/effective)
            stage['turn_reconciliation'].append(dict(city_id=c['city_id'],native=c['turns_until_growth'],
                calculated=calculated,matches=calculated==c['turns_until_growth']))
    if brief and evidence:stage['growth_reading_reduction_percent']=round(100*(1-brief['bytes']/evidence['bytes']),2)
    if full and pages:stage['first_page_reading_reduction_percent']=round(100*(1-pages[0]['bytes']/full['bytes']),2)
    stages.append(stage)

sources=json.loads((ROOT/'evidence/source-before.json').read_text())
source_checks=[dict(name=r['name'],sha256=r['sha256'],record_sha256=r['record_sha256'],unchanged=
    hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']and
    hashlib.sha256(Path(r['registry_path']).read_bytes()).hexdigest()==r['record_sha256'])for r in sources]
code=json.loads((ROOT/'evidence/verified-code-sha256.json').read_text())
code_ok=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in code.items())
checkpoints=[]
for row in records:
    if row['tool']!='save_game'or row['error']:continue
    name=row['args']['save_name'];registry=Path.home()/'.civ6-mcp/checkpoints'/(hashlib.sha256(name.encode()).hexdigest()+'.json')
    record=json.loads(registry.read_text())
    native=Path.home()/"Library/Application Support/Sid Meier's Civilization VI/Sid Meier's Civilization VI/Saves/Single"/(name+'.Civ6Save')
    checkpoints.append(dict(record,file_matches=hashlib.sha256(native.read_bytes()).hexdigest()==record['sha256'],
        identity_matches=record['turn']==row['turn']and record['civilization']=='sumeria'and record['seed']==-1896272000,
        registry_sha256=hashlib.sha256(registry.read_bytes()).hexdigest()))
output=dict(stages=stages,sources=source_checks,verified_code_unchanged=code_ok,checkpoints=checkpoints,
    tool_errors=[dict(metric(r),result=r['text'])for r in records if r['error']],tool_calls=len(records))
(ROOT/'reports/results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(stages=[{k:v for k,v in s.items()if k in ('turn','pages_recover_full','full_count','region_count','growth_reading_reduction_percent')}for s in stages],sources_unchanged=all(r['unchanged']for r in source_checks),code_unchanged=code_ok),ensure_ascii=False))
