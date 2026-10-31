"""Replay pinned baseline production readers in isolated current fixtures only."""
import importlib
import subprocess

import pytest
from civ_mcp import lua as lq

BASE='c9c79aa5c02b244fc61023d9b8e849e96f451a2a'
module=importlib.import_module('civ_mcp.lua.cities')
namespace=dict(vars(module))
source=subprocess.check_output(['git','show',f'{BASE}:src/civ_mcp/lua/cities.py'],text=True)
exec(compile(source,f'{BASE}/cities.py','exec'),namespace)
for name in ('build_city_production_query','parse_city_production_response'):
    setattr(lq,name,namespace[name])
raise SystemExit(pytest.main(['-q','tests/test_production_cost_facts.py']))
