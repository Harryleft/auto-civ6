import json,copy,re
from pathlib import Path
D=Path(__file__).parent
original=json.loads((D/'original.json').read_text()); target=copy.deepcopy(original)
by={n['id']:n for n in target['nodes']}; notes={}
def edit(id,reason,**fields):
 fields={k:v for k,v in fields.items() if not (isinstance(v,(int,float)) and isinstance(by[id].get(k),(int,float)) and abs(by[id][k]-v)<0.001)}
 if fields:by[id].update(fields);notes.setdefault(id,[]).append(reason)
def color(id,field,value,reason):
 value={k:v for k,v in value.items() if not (isinstance(v,str) and k.endswith('_color') and str(by[id][field].get(k,'')).lower()==v.lower())}
 if value:by[id][field].update(value); notes.setdefault(id,[]).append(reason)
P={
 'agent':{'main':'#2E62AE','border':'#A7C3E8','fill':'#E8F0FC'},
 'harness':{'main':'#258067','border':'#9DCDBB','fill':'#E6F3ED'},
 'game':{'main':'#73539E','border':'#C7B3DB','fill':'#EFE8F6'},
 'pending':{'main':'#A47725','border':'#DABC77','fill':'#FFF4DA'},
}
# Preserve shapes, connector geometry, SVG node identities, text, grouping and z-order.
for role,ids in [('agent',['o1:3','o1:4','o1:7','o1:11']),('harness',['o1:5','o1:8']),('game',['o1:6','o1:9','o1:10']),('pending',['o1:12'])]:
 for id in ids:
  color(id,'style',{'border_color':P[role]['border']},'统一同角色边框色')
  if by[id]['style'].get('fill_color','').lower()!='#ffffff':
   color(id,'style',{'fill_color':P[role]['fill']},'统一同角色浅填充色')
for role,ids in [('agent',['c1:1','c1:4','c1:5','c1:11']),('harness',['c1:3','c1:6','c1:7','c1:10']),('game',['c1:2','c1:8','c1:9']),('pending',['c1:12','c1:13'])]:
 for id in ids:color(id,'style',{'border_color':P[role]['main']},'统一箭线语义色')
for role,ids in [('agent',['d1:1','d1:4','d1:5','d1:12']),('harness',['d1:3','d1:6','d1:7','d1:11']),('game',['d1:2','d1:8','d1:10']),('pending',['d1:13','d1:14'])]:
 for id in ids:
  svg=by[id]['image']['token'];svg=re.sub(r'fill="#[0-9A-Fa-f]{6}"',f'fill="{P[role]["main"]}"',svg)
  color(id,'image',{'token':svg},'仅替换原 SVG 箭头色，不改节点类型和几何')
color('d1:9','image',{'token':by['d1:9']['image']['token'].replace('#D7EEE2','#DDEFE6').replace('#72B799','#75B79D')},'判定菱形沿用 Harness 绿色')
for id,center in [('a1:3',225),('a1:5',600),('a1:7',975)]:
 edit(id,'角色标题列中心与基线对齐',x=center-by[id]['width']/2,y=120.175)
for id,center in [('a1:4',225),('a1:6',600),('a1:8',975)]:
 edit(id,'三列说明共用基线',x=center-by[id]['width']/2,y=157.823)
for id,center in [('a1:10',225),('a1:12',600),('a1:14',975)]:
 edit(id,'首行卡片标题居中且共用基线',x=center-by[id]['width']/2,y=302.624)
for id,center in [('a1:17',225),('a1:19',600),('a1:21',975)]:
 edit(id,'第二行卡片标题居中且共用基线',x=center-by[id]['width']/2,y=482.624)
edit('a1:36','双行卡片标题相对顶部间距一致',x=600-by['a1:36']['width']/2,y=887.764)
edit('a1:37','双行卡片说明相对顶部间距一致',x=600-by['a1:37']['width']/2,y=928.823)
for id in ['a1:4','a1:6','a1:8','a1:28','a1:32']:
 color(id,'text',{'text_color':'#657185'},'统一中性说明色')
for id in ['a1:35','a1:38']:
 color(id,'text',{'text_color':P['pending']['main']},'未确认分支保持统一警示色')
for id,x in [('a1:16',241),('a1:25',616),('a1:26',833),('a1:35',616)]:
 edit(id,'垂直箭线旁说明净距统一为16',x=x)
for id,center in [('a1:23',412.5),('a1:24',787.5),('a1:30',780),('a1:33',420)]:
 n=by[id];ratio=18/n['text']['font_size'];w=n['width']*ratio
 color(id,'text',{'font_size':18},'操作说明字号统一为18')
 edit(id,'说明居中并统一距水平箭线18',width=w,height=26.064,x=center-w/2,y=(500 if id in ['a1:23','a1:24'] else 740)-18-26.064)
changes=[]
origby={n['id']:n for n in original['nodes']}
for n in target['nodes']:
 old=origby[n['id']]
 changed={k:{'from':old.get(k),'to':v} for k,v in n.items() if old.get(k)!=v}
 if changed:changes.append({'id':n['id'],'type':n['type'],'text':n.get('text',{}).get('text',''),'reason':list(dict.fromkeys(notes.get(n['id'],[]))),'fields':changed})
assert len(original['nodes'])==len(target['nodes'])==70
assert set(origby)==set(by)
assert all(origby[id].get('text',{}).get('text')==n.get('text',{}).get('text') for id,n in by.items())
assert all(origby[id]['type']==n['type'] for id,n in by.items())
assert all(origby[id].get('connector')==n.get('connector') for id,n in by.items())
(D/'proposed-original-node-values.json').write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n')
(D/'proposed-changes.json').write_text(json.dumps({'status':'NOT_APPLIED','board':'USiHwFpxbhfDZUbwuKMcKQRcn7g','node_count':70,'palette':P,'changes':changes},ensure_ascii=False,indent=2)+'\n')
lines=['# 总体设计画板：原节点样式与对齐修改方案','','状态：仅本地准备，尚未写入飞书。原画板 token `USiHwFpxbhfDZUbwuKMcKQRcn7g`。','','## 约束','','保留 70 个节点的 id、类型、文字、顺序、z_index、connector 完整对象和既有几何。仅调整文字位置、说明字号、颜色。内嵌 SVG 只替换色值。','','## 颜色','','|角色|箭线/标题主色|边框|浅填充|','|---|---|---|---|']
for role,p in P.items():lines.append(f'|{role}|{p["main"]}|{p["border"]}|{p["fill"]}|')
lines+=['','## 几何与字号','','|节点|文字|x 当前→目标|y 当前→目标|其他|','|---|---|---|---|---|']
for c in changes:
 f=c['fields']
 if any(k in f for k in ['x','y','width','height']) or '操作说明字号统一为18' in c['reason']:
  old=origby[c['id']];new=by[c['id']]
  z='；'.join(c['reason'])
  lines.append(f'|{c["id"]}|{c["text"]}|{old["x"]:.3f} → {new["x"]:.3f}|{old["y"]:.3f} → {new["y"]:.3f}|{z}|')
lines+=['','## 写入边界','','`proposed-changes.json` 列出每个字段的旧值和目标值；`proposed-original-node-values.json` 仅为保留原 id 的本地目标快照，不应直接用于 overwrite。','','官方画板概述的方法列表提供读取节点、创建节点和获取主题，未提供原节点 update / PATCH。CLI `+update --overwrite` 会删除全部原节点后重建；不能保证 id、编辑历史和未导出的内部属性，因此不作为本次原位编辑路径。','','可通过原生画板 UI 对原节点修改属性和位置；如 UI 不可操作，应保留方案和原始备份，暂不改板。']
(D/'POLISH-PLAN.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'nodes':70,'changed_nodes':len(changes),'status':'NOT_APPLIED','files':['POLISH-PLAN.md','proposed-changes.json','proposed-original-node-values.json']},ensure_ascii=False))
