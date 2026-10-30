"""On-demand city yield facts; focused Rationalism comparisons, no strategy.

The two documented predicate shapes are compared to current observations only.
Unfamiliar flags/arguments/graphs remain unknown and are available in full.
These comparisons never claim a native modifier receipt or net science gain.
"""
import json
import math
from urllib.parse import unquote

from civ_mcp.lua import city_yields as native
from civ_mcp.snapshot import pending_facts

_ROW_METADATA = {'Index', 'Hash', 'PrimaryKey', 'RowId', 'rowid'}


def _facts(row):
    return {k: v for k, v in row.items() if k not in _ROW_METADATA}

def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rows(rules, table, field=None, value=None):
    rows = list(rules.get(table, {}).values())
    return rows if field is None else [r for r in rows if r.get(field) == value]


def _arguments(rows):
    values = {}
    for row in rows:
        if row.get('Name') in values or row.get('Type') not in (None, 'ARGTYPE_IDENTITY') or any(
            row.get(k) not in (None, '') for k in ('Extra', 'SecondExtra')):
            return None
        values[row['Name']] = row.get('Value')
    return values


def compare_conditions(data):
    """Keep each modifier separate; no universal requirement evaluator."""
    rules = data['rules']
    modifiers = []
    for link in _rows(rules, 'PolicyModifiers'):
        mid = link['ModifierId']
        m = _facts(rules.get('Modifiers', {}).get(mid, {}))
        dynamic = rules.get('DynamicModifiers', {}).get(m.get('ModifierType'), {})
        args = _arguments(_rows(rules, 'ModifierArguments', 'ModifierId', mid))
        flags = {k: v for k, v in m.items() if k not in ('ModifierId', 'ModifierType', 'Index', 'Hash')}
        supported = bool(data['rules_complete'] and args is not None and
            set(args) == {'Amount', 'DistrictType', 'YieldType'} and
            args.get('DistrictType') == 'DISTRICT_CAMPUS' and args.get('YieldType') == 'YIELD_SCIENCE' and
            dynamic.get('EffectType') == 'EFFECT_ADJUST_BUILDING_YIELD_MODIFIERS_FOR_DISTRICT' and
            dynamic.get('CollectionType') == 'COLLECTION_PLAYER_CITIES' and
            all(k in m and m[k] in (0, False) for k in ('NewOnly', 'Permanent', 'Repeatable', 'RunOnce')) and
            all(m.get(k) in (None, 0, False) for k in ('NewOnly', 'Permanent', 'Repeatable', 'RunOnce', 'OwnerStackLimit', 'SubjectStackLimit')) and
            not m.get('OwnerRequirementSetId'))
        supported &= set(m) <= {'ModifierId', 'ModifierType', 'Index', 'Hash', 'NewOnly', 'Permanent',
            'Repeatable', 'RunOnce', 'OwnerStackLimit', 'SubjectStackLimit', 'OwnerRequirementSetId', 'SubjectRequirementSetId'}
        sid = m.get('SubjectRequirementSetId')
        requirement_set = _facts(rules.get('RequirementSets', {}).get(sid, {}))
        requirements = []
        for rel in _rows(rules, 'RequirementSetRequirements', 'RequirementSetId', sid):
            rid = rel['RequirementId']
            row = _facts(rules.get('Requirements', {}).get(rid, {}))
            rargs = _arguments(_rows(rules, 'RequirementArguments', 'RequirementId', rid))
            spec = dict(id=rid, definition=row, arguments=rargs)
            requirements.append(spec)
        # Rationalism's documented graph has one simple city predicate per
        # branch. Any extra/nested condition is displayed but not silently run.
        supported &= requirement_set.get('RequirementSetType') == 'REQUIREMENTSET_TEST_ALL' and len(requirements) == 1
        r = requirements[0] if len(requirements) == 1 else {}
        definition, rargs = r.get('definition', {}), r.get('arguments')
        supported &= bool(definition and rargs is not None and all(
            k in definition and definition[k] in (0, False) for k in ('Inverse', 'Reverse', 'Triggered', 'Persistent')))
        supported &= set(definition) <= {'RequirementId', 'RequirementType', 'Index', 'Hash', 'Inverse',
            'Reverse', 'Triggered', 'Persistent', 'Impact', 'Likeliness', 'ProgressWeight'}
        shape = definition.get('RequirementType')
        expected = {'Amount'} if shape == 'REQUIREMENT_CITY_HAS_X_POPULATION' else (
            {'Amount', 'DistrictType', 'YieldType'} if shape == 'REQUIREMENT_CITY_HAS_HIGH_ADJACENCY_DISTRICT' else set())
        supported &= bool(expected and rargs is not None and set(rargs) == expected)
        if shape == 'REQUIREMENT_CITY_HAS_HIGH_ADJACENCY_DISTRICT':
            supported &= bool(rargs and rargs.get('DistrictType') == 'DISTRICT_CAMPUS' and rargs.get('YieldType') == 'YIELD_SCIENCE')
        try:
            threshold = float((rargs or {})['Amount'])
            amount = float((args or {})['Amount'])
            supported &= math.isfinite(threshold) and math.isfinite(amount)
        except (KeyError, TypeError, ValueError):
            threshold = amount = None; supported = False
        branch = dict(modifier_id=mid, amount_percent=amount, target=(args or {}), flags=flags,
                      requirement_set=requirement_set, requirements=requirements,
                      comparison_supported=bool(supported))
        modifiers.append(branch)
        for city in data['cities']:
            campuses = [d for d in city['districts'] if d['district_type'] == 'DISTRICT_CAMPUS']
            observed = city['population'] if shape == 'REQUIREMENT_CITY_HAS_X_POPULATION' else (
                campuses[0]['plot_adjacency'].get('YIELD_SCIENCE') if len(campuses) == 1 else None)
            met = observed >= threshold if supported and _number(observed) else None
            plot_observed = None
            if shape == 'REQUIREMENT_CITY_HAS_HIGH_ADJACENCY_DISTRICT' and len(campuses) == 1:
                plot_observed = campuses[0].get('plot_adjacency', {}).get('YIELD_SCIENCE')
            if supported and shape == 'REQUIREMENT_CITY_HAS_HIGH_ADJACENCY_DISTRICT' and not campuses:
                met = False if data['scan_complete'] else None
            city.setdefault('policy_conditions', []).append(dict(modifier_id=mid, observed=observed,
                plot_observed=plot_observed, threshold=threshold, meets_observed_threshold=met,
                basis='observed population' if shape == 'REQUIREMENT_CITY_HAS_X_POPULATION' else 'plot:GetAdjacencyYield (comparison, not engine state)',
                native_conditions_met=None,
                native_effect_confirmed=None))
    data['modifiers'] = modifiers
    for city in data['cities']:
        campus = [d for d in city['districts'] if d['district_type'] == 'DISTRICT_CAMPUS']
        city['campus_status'] = 'absent' if not campus and data['scan_complete'] else (
            'complete' if len(campus) == 1 and campus[0]['complete'] is True and campus[0]['pillaged'] is False else 'unavailable_or_unknown')
        city['campus_buildings'] = [b for b in city['buildings'] if b['district_type'] == 'DISTRICT_CAMPUS']
    return data


def bind_engine_conditions(data):
    """Interpret engine observations after exact owned-city component binding."""
    rows = data.get('engine', [])
    if not data.get('stable_world_turn') or not rows or rows[-1] != ['CY_ENGINE_SCAN', 'true', '~']:
        return
    mods = {r[1]: r for r in rows if r[0] == 'CY_ENGINE_MOD'}
    objects = [r for r in rows if r[0] == 'CY_ENGINE_OBJECT']
    sets = {(r[1], r[2]): r for r in rows if r[0] == 'CY_ENGINE_SET'}
    requirements = {}
    for r in rows:
        if r[0] == 'CY_ENGINE_REQUIREMENT':
            requirements.setdefault((r[1], r[2]), []).append(r)
    definitions = {m['modifier_id']: m for m in data['modifiers']}
    for city in data['cities']:
        for cond in city.get('policy_conditions', []):
            mid = cond['modifier_id']; definition = definitions[mid]
            instances = [m for m in mods.values() if m[2] == mid]
            if len(instances) != 1 or not definition['comparison_supported']:
                continue
            inst = instances[0]
            targets = [o for o in objects if o[1] == inst[1] and o[7] == str(city['city_id']) and o[8] == str(city['owner'])]
            if len(targets) != 1:
                continue
            obj = targets[0]; key = (inst[1], obj[2]); state = sets.get(key)
            reqs = requirements.get(key, [])
            expected = {r['id'] for r in definition['requirements']}
            if not state or state[5] != definition['requirement_set']['RequirementSetId'] or len(reqs) != len(expected) or {r[4] for r in reqs} != expected:
                continue
            native_met = {'Met': True, 'NotMet': False}.get(state[4])
            if native_met is None or obj[6] != str(native_met).lower() or any(r[5] != state[4] for r in reqs):
                continue
            if inst[3] not in ('true', 'false'):
                continue
            cond.update(native_conditions_met=native_met,
                        native_effect_confirmed=inst[3] == 'true' and native_met,
                        native_requirement_state=state[4], native_modifier_instance=inst[1],
                        native_object_id=obj[2], native_requirement_instance=state[3])
            if cond['meets_observed_threshold'] is not None and cond['meets_observed_threshold'] != native_met:
                cond['native_effect_confirmed'] = None
                data['status'] = 'unknown'
                data['errors'].append(dict(section='native_condition', error=f"{city['city_id']} {mid}: native condition disagrees with observed threshold"))


def brief(data):
    def number(v):
        return 'unknown' if v is None else str(v).removesuffix('.0')
    def state(v):
        return {True: 'yes', False: 'no', None: 'unknown'}[v]
    p = data['policy']
    lines = [f"City yields: {data['status']} | {data.get('identity')} | current owned cities",
             'Sources: native district/plot yields; current GameInfo rules; GameCore requirement instances when slotted.',
             f"Rationalism: unlocked {state(p.get('unlocked'))}; can slot {state(p.get('can_slot'))}; slotted {state(p.get('slotted'))}."]
    for m in data['modifiers']:
        args = m['target']
        lines.append(f"  {m['modifier_id']}: {number(m['amount_percent'])}% {args.get('YieldType', 'unknown')} from {args.get('DistrictType', 'unknown')} buildings; "
                     f"comparison {'supported' if m['comparison_supported'] else 'unknown'}; flags {json.dumps(m['flags'], ensure_ascii=False, separators=(',', ':'))}; "
                     f"{json.dumps(m['requirement_set'], ensure_ascii=False, separators=(',', ':'))}; "
                     f"{json.dumps(m['requirements'], ensure_ascii=False, separators=(',', ':'))}")
    for c in data['cities']:
        lines.append(f"{c['name']} [id:{c['city_id']}] pop {number(c['population'])}; science {number(c['science'])}; food surplus {number(c['food_surplus'])}; campus {c['campus_status']}.")
        for d in c['districts']:
            if d['district_type'] == 'DISTRICT_CITY_CENTER':
                continue
            fields = []
            for label, values in [('district_adj', d['adjacency']), ('plot_adj', d['plot_adjacency']), ('yield', d['yields'])]:
                facts = ','.join(f"{k.removeprefix('YIELD_')}={number(v)}" for k, v in values.items() if v is None or v != 0)
                fields.append(f"{label} {facts or 'all six yields 0'}")
            lines.append(f"  {d['district_type']} [id:{d['district_id']}] ({d['x']},{d['y']}) complete {state(d['complete'])}, pillaged {state(d['pillaged'])}; " + '; '.join(fields))
        if c['campus_buildings']:
            lines.append('  Campus buildings: ' + ', '.join(f"{b['building_type']} (pillaged {state(b['pillaged'])})" for b in c['campus_buildings']))
        for cond in c.get('policy_conditions', []):
            lines.append(f"  {cond['modifier_id']}: observed {number(cond['observed'])} / required {number(cond['threshold'])}; threshold met {state(cond['meets_observed_threshold'])}; native condition {state(cond['native_conditions_met'])}; native effect {state(cond['native_effect_confirmed'])}.")
    lines.append('Threshold comparisons use population/plot adjacency and are inferences; native condition/effect are separate engine observations, never net science gain. Full current rules/receipts and zero yields: --view structured; science tooltips: --view evidence.')
    if not data['rules_complete']:
        lines.append('Current rule graph incomplete; all comparisons unknown.')
    for error in data['errors']:
        lines.append(f"Unavailable {error['section']}: {error['error']}")
    return '\n'.join(lines)


async def read_context(game, city_id=None, view='brief'):
    if view not in ('brief', 'structured', 'evidence'):
        raise ValueError('City yield view must be brief, structured or evidence')
    code = native.build_query(city_id, evidence=view == 'evidence')
    pending = pending_facts(game)
    if any(pending.values()):
        return dict(schema_version=1, status='blocked', pending=pending,
                    error='Pending operation exists; confirm its original request first.')
    rows = await game.conn.execute_write(code, timeout=30.0)
    data = native.parse_response(rows)
    engine_rows, final_rows = None, None
    if view == 'evidence' or data['policy'].get('slotted') is True:
        engine_rows = await game.conn.execute_read(native.build_engine_query(city_id), timeout=30.0)
        data['engine'] = [[unquote(v) for v in row.split('|')]
                          for row in engine_rows if row.startswith('CY_ENGINE_')]
        final_rows = await game.conn.execute_write(native.LUA_TURN_WORLD + '\nlocal before=turnWorld();printTurnWorld(before)\n' + f'print({native.SENTINEL!r})', timeout=30.0)
    from civ_mcp.end_turn import _world_observation
    try:
        world, turn = _world_observation(rows)
        civ, seed, owner = world
        witnesses = [r for r in rows if r.startswith('TURN_WORLD_SEEN|')]
        stable = witnesses == [f'TURN_WORLD_SEEN|{civ}|{seed}|{owner}|{turn}'] * 2
        data['identity'] = dict(civilization=civ, seed=seed, player_id=owner, turn=turn)
        stable &= all(c['owner'] == owner for c in data['cities'])
        for extra in (engine_rows, final_rows):
            if extra is not None:
                stable &= _world_observation(extra) == (world, turn)
    except ValueError:
        stable = False; data['identity'] = None
    data.update(schema_version=1, stable_world_turn=stable, pending=pending,
                scope='owned cities, placed districts including unfinished; current GameInfo rules; not atomic')
    data['native_fields_complete'] = all(
        all(c[k] is not None for k in ('population', 'science', 'food_surplus')) and
        all(d['complete'] is not None and d['pillaged'] is not None and
            all(v is not None for v in (*d['adjacency'].values(), *d['plot_adjacency'].values(), *d['yields'].values())) for d in c['districts']) and
        all(b['pillaged'] is not None for b in c['buildings']) for c in data['cities'])
    complete = stable and data['scan_complete'] and data['requested_city_found'] and data['rules_complete'] and data['native_fields_complete'] and bool(data['policy']) and all(v is not None for v in data['policy'].values())
    data['status'] = 'ready' if complete else 'unknown'
    if not stable or not data['scan_complete']:
        data['rules_complete'] = False  # Unstable observations cannot support comparisons.
    compare_conditions(data)
    bind_engine_conditions(data)
    data['native_conditions_complete'] = None
    if data['policy'].get('slotted') is True:
        data['native_conditions_complete'] = all(
            c['native_conditions_met'] is not None and c['native_effect_confirmed'] is not None
            for city in data['cities'] for c in city.get('policy_conditions', []))
        if not data['native_conditions_complete']:
            data['status'] = 'unknown'
            data['errors'].append(dict(section='native_condition', error='Equipped policy conditions are incomplete or unbound; no effect confirmed.'))
    if view == 'brief':
        return dict(schema_version=1, status=data['status'], report=brief(data))
    return data
