"""Resource-search display only; preserve native facts and structured expansion."""
import json

_CLASSES={'RESOURCECLASS_BONUS':'B','RESOURCECLASS_LUXURY':'L','RESOURCECLASS_STRATEGIC':'S','RESOURCECLASS_ARTIFACT':'A'}
_ACTORS={'UNIT_BUILDER':'B','UNIT_ARCHAEOLOGIST':'A'}


def brief(result, *, search_kind='resource', query='', limit=50, offset=0):
    out={k:v for k,v in result.items()if k!='data'}
    data=result.get('data')or {}
    rows=data.get('locations')
    lines=['Class B=RESOURCECLASS_BONUS, L=RESOURCECLASS_LUXURY, S=RESOURCECLASS_STRATEGIC, A=RESOURCECLASS_ARTIFACT.',
        'Actor B=UNIT_BUILDER, A=UNIT_ARCHAEOLOGIST; type only, eligibility unverified. ?=unknown. Owner -1=unclaimed, other numbers=player IDs.']
    scopes=[]
    for row in rows or []:
        value=row.get('scope')
        if value not in scopes:scopes.append(value)
    for i,value in enumerate(scopes,1):lines.append(f'Row scope S{i}: {value if value is not None else "unknown"}')
    def value(v):
        if v is None:return '?'
        if type(v)is bool:return 'yes'if v else 'no'
        return str(v)
    fields=('owner_id','visible_now','improvement_type','pillaged','work_unit','details_status')
    labels=('owner','visible','facility','pillaged','actor','details')
    for row in rows or []:
        cls=_CLASSES.get(row.get('class'),row.get('class'))
        prefix=f"({value(row.get('x'))},{value(row.get('y'))}) {value(row.get('type'))} {value(row.get('name'))}"
        if 'class'in row:prefix+=f' [{value(cls)}]'
        details=[]
        for key,label in zip(fields,labels):
            if key in row:
                v=row[key]
                if key=='work_unit':v=_ACTORS.get(v,v)
                details.append(f'{label}={value(v)}')
        if len(scopes)>1:details.append(f'scope=S{scopes.index(row.get("scope"))+1}')
        known={'x','y','type','name','class','scope',*fields}
        extra={k:v for k,v in row.items()if k not in known}
        if extra:details.append('extra='+json.dumps(extra,ensure_ascii=False,allow_nan=False,separators=(',',':')))
        lines.append(prefix+'; '+'; '.join(details))
    if rows is None:lines.append('Native locations unknown; no complete absence established.')
    elif not rows:lines.append('No locations in this page; inspect total/offset and unknown_fields.')
    out.update(view='brief',shown=len(rows)if rows is not None else None,scope=data.get('scope'),report='\n'.join(lines),
        expand=dict(command='map search',search_kind=search_kind,query=query,limit=limit,offset=offset,view='structured'))
    extra={k:v for k,v in data.items()if k not in ('locations','scope')}
    if extra:out['data_extra']=extra
    return out
