"""Offline fixed-fixture probes against b619fe3f; never opens the game.

Run from repository root with uv run --locked python PATH. Expected failures
are before evidence, not a product test gate. Product regression uses pytest.
"""
import ast
import importlib
import subprocess
import sys
import textwrap

import pytest

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState

BASE = 'b619fe3fd9df59683c2f1e45cff5a621d93a4747'
for module in ('map', 'cities', 'units', 'great_people', 'governance'):
    domain = importlib.import_module('civ_mcp.lua.' + module)
    source = subprocess.check_output(['git', 'show', f'{BASE}:src/civ_mcp/lua/{module}.py'], text=True)
    namespace = dict(vars(domain))
    exec(compile(source, f'{BASE}/{module}.py', 'exec'), namespace)
    for name, value in namespace.items():
        if name.startswith(('build_', 'parse_')) and callable(value):
            if hasattr(lq, name):
                setattr(lq, name, value)
            setattr(domain, name, value)
source = subprocess.check_output(['git', 'show', f'{BASE}:src/civ_mcp/game_state.py'], text=True)
cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'GameState')
for name in ('get_wonder_advisor', 'get_purchasable_tiles', 'get_gp_advisor', 'get_policies'):
    node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    namespace = {'lq': lq}
    exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    setattr(GameState, name, namespace[name])
selection = [
    'tests/test_district_advisor_native.py::test_native_adjacency_overrides_the_old_incomplete_terrain_formula',
    'tests/test_district_advisor_native.py::test_all_native_placements_are_returned_instead_of_silent_top_ten',
    'tests/test_district_advisor_native.py::test_unavailable_native_yield_is_unknown_not_a_zero_or_handwritten_fallback',
    'tests/test_wonder_advisor_native.py',
    'tests/test_tile_purchase_advice.py',
    'tests/test_great_people_advice.py',
    'tests/test_gp_advisor_native.py',
    'tests/test_city_attack_advice.py',
    'tests/test_builder_advice_scope.py',
    'tests/test_settle_advice_scope.py::test_hidden_city_never_changes_or_breaks_known_candidates_and_no_silent_top_ten',
    'tests/test_settle_advice_scope.py::test_native_loyalty_warning_is_distinct_from_absent_unknown_and_no_weighted_rank',
    'tests/test_policy_advice.py::test_zero_cost_and_native_slot_permission_do_not_override_changed_this_turn',
    'tests/test_policy_advice.py::test_obsolete_cards_are_excluded_from_default_and_explicit_inspection_explains_why',
    'tests/test_pathing_observation.py',
    'tests/test_attack_mode_lua.py::test_unit_candidate_board_never_exposes_a_hidden_or_unverified_unit',
    'tests/test_attack_mode_lua.py::test_adjacent_ranged_target_still_requires_native_attack_eligibility',
    'tests/test_city_attack_no_los.py::test_unrelated_native_array_cannot_authorize_a_city_shot',
]
raise SystemExit(pytest.main(['-q', *selection]))
