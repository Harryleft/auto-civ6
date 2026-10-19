from pathlib import Path
import json,subprocess,xml.etree.ElementTree as E,re
p=Path(__file__).resolve().parent
doc='redacted-document-001'
ops=json.load(open(p/'patch-plan.json'))
def root(d):return E.fromstring('<root>'+d['content']+'</root>')
def txt(e):return ''.join(e.itertext())
def find(es,prefix):
 m=[i for i,e in enumerate(es) if txt(e).startswith(prefix)]
 assert len(m)==1,(prefix,m)
 return m[0]
def bounds(es,op):
 a=find(es,op['start']);b=find(es,op['end']) if op['end'] else a
 assert b>=a
 assert not any(e.tag in ['whiteboard','img'] for e in es[a:b+1])
 return a,b
def fetch(out):
 r=subprocess.run(['lark-cli','docs','+fetch','--as','user','--doc',doc,'--detail','full'],capture_output=True,text=True,check=True)
 j=json.loads(r.stdout);assert j['ok'];out.write_text(r.stdout);return j['data']['document']
def resources(r):return [(e.tag,e.get('token') or e.get('src'),e.get('id')) for e in r if e.tag in ['whiteboard','img']]
d=json.load(open(p/'before.json'))['data']['document'];old=root(d);expected=root(d)
for op in ops:
 es=list(expected);a,b=bounds(es,op)
 replacement=E.fromstring('<root>'+op['content']+'</root>')
 for e in es[a:b+1]:expected.remove(e)
 for offset,e in enumerate(replacement):expected.insert(a+offset,e)
(p/'expected.xml').write_text(''.join(E.tostring(e,encoding='unicode') for e in expected))
print('before chars',len(re.sub(r'\s+','',''.join(old.itertext()))),'after chars',len(re.sub(r'\s+','',''.join(expected.itertext()))),flush=True)
for n,op in enumerate(ops,1):
 current=fetch(p/'latest.json');r=root(current);es=list(r);a,b=bounds(es,op)
 path=p/f'patch-{n:02}.xml';path.write_text(op['content'])
 argv=['lark-cli','docs','+update','--as','user','--doc',doc,'--doc-format','xml','--command','block_replace','--revision-id',str(current['revision_id']),'--content','@'+str(path)]
 if a==b:argv+=['--block-id',es[a].get('id')]
 else:argv+=['--start-block-id',es[a].get('id'),'--end-block-id',es[b].get('id')]
 out=subprocess.run(argv,capture_output=True,text=True);(p/f'patch-{n:02}-result.json').write_text(out.stdout or out.stderr)
 assert out.returncode==0,(n,out.stderr)
 j=json.loads(out.stdout);assert j['ok'] and j['data'].get('result')=='success',j
 assert not j['data'].get('warnings'),j
 after=fetch(p/f'patch-{n:02}-readback.json');assert resources(root(after))==resources(old),(n,'resource change')
 print('patched',n,op['start'],flush=True)
final=fetch(p/'after.json');fin=root(final)
norm=lambda e:re.sub(r'\s+','',''.join(e.itertext()))
assert norm(fin)==norm(expected),'body mismatch'
assert resources(fin)==resources(old)
(p/'after.xml').write_text(final['content'])
checks={'document_id':doc,'revision_id':final['revision_id'],'body_matches_expected':True,'resource_ids_and_tokens_unchanged':True,'before_chars':len(norm(old)),'after_chars':len(norm(fin)),'patches':len(ops),'headings':[txt(e) for e in fin if e.tag in ['h1','h2']]}
(p/'body-verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print(json.dumps(checks,ensure_ascii=False),flush=True)
