"""Read saved CLI evidence only. No game, network, mutations or decisions."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]

def records(path):
    started = {}; phase = None; result = []
    for line in path.read_text().splitlines():
        row=json.loads(line)
        if row.get('event')=='tool_started': started[row['sequence']]=row
        if row.get('event')!='tool_result': continue
        text='\n'.join(c.get('text','') for c in row['result'].get('content',[]))
        if row['tool']=='load_game_save' and not row.get('is_error'):
            match=re.search(r'\bturn (\d+)\.',text)
            if match: phase=int(match[1])
        start=started.get(row['request'],{})
        elapsed=None
        if start:
            elapsed=(datetime.fromisoformat(row['timestamp'])-datetime.fromisoformat(start['timestamp'])).total_seconds()
        result.append(dict(turn=phase, tool=row['tool'], args=start.get('arguments',{}), sequence=row['sequence'],
            text=text, bytes=len(text.encode()), rpc_seconds=elapsed, error=row.get('is_error',False), source=str(path.relative_to(ROOT))))
    return result

all_records=[]
for name in ('live-audit','live-final-v1','live-final-v2','live-complete','live-accepted','live-verified'):
    path=ROOT/'evidence'/name/'debug.jsonl'
    if path.exists(): all_records.extend(records(path))
verified=[r for r in all_records if r['source'].startswith('evidence/live-verified/')]

def first(rows,tool,predicate=lambda r:True):
    return next((r for r in rows if r['tool']==tool and predicate(r)),None)

def compact(record):
    return {k:v for k,v in record.items() if k!='text'} if record else None

stages=[]
for turn in (50,100,150,200):
    rows=[r for r in verified if r['turn']==turn]
    if not rows: continue
    brief=first(rows,'get_city_yields',lambda r:'city_id'not in r['args']and r['args'].get('view','brief')=='brief')
    structured=first(rows,'get_city_yields',lambda r:'city_id'not in r['args']and r['args'].get('view')=='structured')
    single=first(rows,'get_city_yields',lambda r:'city_id'in r['args']and r['args'].get('view','brief')=='brief')
    audit=first(rows,'get_city_yields',lambda r:'city_id'in r['args']and r['args'].get('view')=='evidence')
    default=first(rows,'get_governors',lambda r:not r['args'].get('include_ineligible'))
    expanded=first(rows,'get_governors',lambda r:r['args'].get('include_ineligible')is True)
    policy=first(rows,'get_policies',lambda r:'policy_type'not in r['args'])
    selected=first(rows,'get_policies',lambda r:r['args'].get('policy_type')=='POLICY_SURVEY')
    s=dict(turn=turn, brief_all=compact(brief),brief_city=compact(single),evidence_city=compact(audit),
        governors_brief=compact(default),governors_full=compact(expanded),policies_all=compact(policy),policy_selected=compact(selected),
        district=compact(first(rows,'get_district_advisor')),wonder=compact(first(rows,'get_wonder_advisor')),
        tiles=compact(first(rows,'get_purchasable_tiles')),settle=compact(first(rows,'get_global_settle_advisor')))
    if structured:
        data=json.loads(structured['text']);s['status']=data['status'];s['cities']=data['cities'];s['identity']=data['identity']
        s['native_conditions_complete']=data['native_conditions_complete']
    for prefix,small,large in [('governor',default,expanded),('city',single,audit),('policy',selected,policy)]:
        if small and large:s[prefix+'_reading_reduction_percent']=round(100*(1-small['bytes']/large['bytes']),2)
    stages.append(s)
receipts=[compact(r)|{'result':r['text']}for r in all_records if r['tool']in ('purchase_tile','patronize_great_person','set_policies','promote_governor')]
saves=[compact(r)|{'result':r['text']}for r in all_records if r['tool']=='save_game']
original=json.loads((ROOT/'evidence/source-before.json').read_text())
source_check=[]
for row in original:
    source_check.append(dict(name=row['name'],sha256=row['sha256'],record_sha256=row['record_sha256'],
        unchanged=hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256'] and
        hashlib.sha256(Path(row['registry_path']).read_bytes()).hexdigest()==row['record_sha256']))
code=json.loads((ROOT/'evidence/verified-code-sha256.json').read_text())
code_check=all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in code.items())
output=dict(stages=stages,receipts=receipts,saves=saves,sources=source_check,
    verified_code_unchanged=code_check,observed_read_tools=sorted({r['tool']for r in all_records if r['tool'].startswith(('get_','search_','list_'))}))
checkpoints=[]
for saved in saves:
    if not saved['source'].startswith('evidence/live-verified/') or saved['error']: continue
    name=saved['args']['save_name']
    registry=Path.home()/'.civ6-mcp/checkpoints'/(hashlib.sha256(name.encode()).hexdigest()+'.json')
    record=json.loads(registry.read_text())
    native=Path.home()/'Library/Application Support/Sid Meier\'s Civilization VI/Sid Meier\'s Civilization VI/Saves/Single'/(name+'.Civ6Save')
    checkpoints.append(dict(record,record_sha256=hashlib.sha256(registry.read_bytes()).hexdigest(),
        file_matches=hashlib.sha256(native.read_bytes()).hexdigest()==record['sha256'],
        identity_matches=record['turn']==saved['turn'] and record['civilization']=='sumeria' and record['seed']==-1896272000))
output['verified_checkpoints']=checkpoints
output['action_sources_equal_to_verified']={}
for name in ('live-final-v2','live-complete','live-accepted'):
    context=json.loads((ROOT/'evidence'/name/'context.json').read_text())
    before=context.get('source_sha256',{})
    output['action_sources_equal_to_verified'][name]={path:before.get(path)==code.get(path) for path in (
        'src/civ_mcp/actions.py','src/civ_mcp/game_state.py','src/civ_mcp/lua/tile_purchase.py',
        'src/civ_mcp/lua/tile_facts.py','src/civ_mcp/lua/great_person_pool.py',
        'src/civ_mcp/lua/governors.py','src/civ_mcp/lua/policies.py')}
(ROOT/'reports/results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(stages=[{k:v for k,v in s.items()if k in ('turn','status','governor_reading_reduction_percent','city_reading_reduction_percent','policy_reading_reduction_percent')}for s in stages],sources_unchanged=all(s['unchanged']for s in source_check),code_unchanged=code_check),ensure_ascii=False))
