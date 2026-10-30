"""Compare pinned baseline/current queries in an isolated fixture; no game access."""
import asyncio,json,subprocess,sys
sys.path.insert(0,'tests')
from test_governor_confirmation import GovernorConn
from civ_mcp.lua import governance
from civ_mcp.narrate import narrate_governors

BASE='b619fe3fd9df59683c2f1e45cff5a621d93a4747'
namespace=dict(vars(governance))
source=subprocess.check_output(['git','show',f'{BASE}:src/civ_mcp/lua/governance.py'],text=True)
exec(compile(source,f'{BASE}/governance.py','exec'),namespace)
results=[]
for version,build,parse in (
    (BASE,namespace['build_governors_query'],namespace['parse_governors_response']),
    ('current',governance.build_governors_query,governance.parse_governors_response),
):
    conn=GovernorConn();conn.lua.execute('addGovernor(101);eligible=false')
    status=parse(asyncio.run(conn.execute_write(build())))
    results.append({'version':version,
        'native_can_earn':bool(conn.lua.eval("Players[0]:GetGovernors():CanEarnPromotion(101,212)")),
        'displayed_as_available':[p.promotion_type for g in status.appointed for p in g.available_promotions],
        'report':narrate_governors(status),'submitted':int(conn.lua.eval('#submissions'))})
print(json.dumps(results,ensure_ascii=False,indent=2))
