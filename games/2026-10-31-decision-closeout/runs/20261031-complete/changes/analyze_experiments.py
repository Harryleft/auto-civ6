#!/usr/bin/env python3
"""Analyze this run's fixed A/B/A CLI evidence without contacting the game.

Run from any directory; paths are relative to this run directory:
  uv run --locked python changes/analyze_experiments.py --live evidence/live-v4
  uv run --locked python changes/analyze_experiments.py --live evidence/live-v4 \
      --output evidence/experiments-analysis.json

Writing an output requires a cleanly closed session and complete observations.
This is an evidence parser, not a policy calculator or a gameplay controller.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


RUN = Path(__file__).resolve().parents[1]
CITY = 131073
BASE200 = 'closeout_v4_t200_result_20261031'
BASE201 = 'closeout_v4_t201_policy_a_20261031'


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else RUN / path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check(name, passed, detail=None):
    result = dict(name=name, status='PASS' if passed else 'FAIL')
    if detail is not None:
        result['detail'] = detail
    return result


def result_status(checks):
    return 'PASS' if checks and all(c['status'] == 'PASS' for c in checks) else 'FAIL'


def ref(call):
    return dict(request=call['request'], command=call['command'], tool=call['tool'])


def load(live):
    path = live / 'debug.jsonl'
    raw = path.read_bytes()
    calls, starts, errors, command, events = [], {}, [], '', []
    for line, row in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(row)
            events.append(event)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(dict(line=line, error=str(exc)))
            continue
        kind = event.get('event')
        if kind == 'command':
            command = event['input']
        elif kind == 'tool_started':
            starts[event['sequence']] = (event, command)
        elif kind == 'tool_result':
            pair = starts.pop(event.get('request'), None)
            if pair is None:
                errors.append(dict(line=line, error='Unmatched tool result.'))
                continue
            start, cmd = pair
            text = '\n'.join(c.get('text', '') for c in event.get('result', {}).get('content', [])
                             if c.get('type') == 'text')
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            calls.append(dict(request=start['sequence'], tool=start['tool'], args=start.get('arguments', {}),
                              command=cmd, text=text, data=data,
                              is_error=bool(event.get('is_error') or event.get('protocol_is_error'))))
    summary_file = live / 'summary.json'
    summary = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    state = summary.get('data', {})
    closed = (state.get('mcp_session_closed') is True
              and state.get('runtime', {}).get('has_pending') is False
              and state.get('unresolved_on_exit') is False)
    return calls, dict(file=str(path), sha256=digest(raw), bytes=len(raw), event_count=len(events),
                       run_ids=sorted({e['run_id'] for e in events if e.get('run_id')}),
                       parse_errors=errors, pending_tool_requests=sorted(starts), clean_session_close=closed,
                       summary_sha256=digest(summary_file.read_bytes()) if summary_file.exists() else None)


def find(calls, tool, **args):
    return [c for c in calls if c['tool'] == tool and all(c['args'].get(k) == v for k, v in args.items())]


def latest(calls, tool, **args):
    found = find(calls, tool, **args)
    if not found:
        raise ValueError(f'Missing {tool} with {args}.')
    return found[-1]


def after(calls, low, high=None):
    return [c for c in calls if low < c['request'] and (high is None or c['request'] < high)]


def cities(data):
    return {row['city_id']: row for row in data['cities']}


def science_free(value):
    """Remove the outcomes, retain district, building, and other yield controls."""
    value = deepcopy(value)
    for city in value:
        city.pop('science', None)
        city.pop('science_tooltip', None)
        for key in ('buildings', 'campus_buildings'):
            for building in city.get(key, []):
                building.get('yields', {}).pop('YIELD_SCIENCE', None)
        for condition in city.get('policy_conditions', []):
            # Native requirement state is unavailable when its policy is not
            # slotted; the observed threshold inputs remain a useful control.
            for key in list(condition):
                if key.startswith('native_'):
                    condition.pop(key)
    return value


def stable_engine_snapshot(data):
    """Requirement *instance* handles can be recreated by a save reload.

    Only this transport identity is excluded. Modifier/set/object bindings,
    requirement IDs and states, city data, scope, completeness and all numeric
    outcomes remain compared. The raw differences are separately disclosed.
    """
    value = deepcopy(data)
    for row in value.get('engine', []):
        if row[0] == 'CY_ENGINE_REQUIREMENT':
            row[3] = '<requirement-instance-handle>'
    return value


def requirement_handle_changes(a, restored):
    a_rows = [row for row in a.get('engine', []) if row[0] == 'CY_ENGINE_REQUIREMENT']
    r_rows = [row for row in restored.get('engine', []) if row[0] == 'CY_ENGINE_REQUIREMENT']
    return [dict(modifier_instance=x[1], object_id=x[2], requirement_id=x[4], state_A=x[5],
                 state_A_restored=y[5], handle_A=x[3], handle_A_restored=y[3])
            for x, y in zip(a_rows, r_rows) if x[3] != y[3]]


def noncampus_science(data):
    return {city['city_id']: {b['building_type']: b['yields'].get('YIELD_SCIENCE')
                              for b in city['buildings'] if b['district_type'] != 'DISTRICT_CAMPUS'}
            for city in data['cities']}


def growth_controls(data):
    value = deepcopy(data['cities'])
    for city in value:
        city.get('yield_tooltips', {}).pop('SCIENCE', None)
    return value


def slots(text):
    return {int(m[1]): dict(slot_type=m[2], policy=m[3])
            for m in re.finditer(r'^  Slot (\d+) \(([^)]+)\): .* \((POLICY_\w+)\)$', text, re.M)}


def city_list(text):
    rows, current = {}, None
    for line in text.splitlines():
        match = re.search(r'^  (.+) \(pop (\d+)\) at \((\d+),(\d+)\).*\[id:(\d+)\]$', line)
        if match:
            ident = int(match[5])
            current = rows[ident] = dict(name=match[1], population=int(match[2]), x=int(match[3]), y=int(match[4]),
                                         yields={}, districts='', buildings=[])
            values = re.search(r' — Food (\S+) Prod (\S+) Gold (\S+) Sci (\S+) Cul (\S+) \|', line)
            if values:
                current['yields'] = {k: float(v) for k, v in zip(('food', 'production', 'gold', 'science', 'culture'), values.groups())}
            housing = re.search(r'\| Housing (\S+) Amenities (\S+) \|', line)
            if housing:
                current.update(housing=float(housing[1]), amenities=float(housing[2]))
            build = re.search(r'\| Building: (.*?) \|', line)
            current['current_production'] = build[1] if build else None
        elif current is not None and line.startswith('    Districts: '):
            current['districts'] = line.removeprefix('    Districts: ')
        elif current is not None and line.startswith('    Buildings: '):
            current['buildings'] = line.removeprefix('    Buildings: ').split(', ')
    if not rows:
        raise ValueError('No city list rows parsed.')
    return rows


def list_controls(rows):
    return {ident: {k: row[k] for k in ('name', 'population', 'x', 'y', 'housing', 'amenities', 'districts', 'buildings')}
            for ident, row in rows.items()}


def mutation_trace(calls, low, high):
    return [ref(c) for c in after(calls, low, high)
            if not c['tool'].startswith(('get_', 'list_', 'search_', 'check_', 'confirm_'))
            and c['tool'] != 'save_game']


def focus_phase(calls):
    workforce = latest(calls, 'get_city_workforce', city_id=CITY, view='evidence')
    growth = latest(calls, 'get_city_growth', view='evidence')
    listing = latest(calls, 'get_cities')
    production = latest(calls, 'get_city_production', city_id=CITY, view='structured')
    bomber = next(o for o in production['data']['options'] if o['item_name'] == 'UNIT_BOMBER')
    return dict(workforce=workforce, growth=growth, listing=listing, production=production,
                worker=workforce['data'], growth_city=cities(growth['data'])[CITY],
                city_list=city_list(listing['text']), bomber=bomber)


def focus_metrics(phase):
    g = phase['growth_city']
    return dict(identity=phase['worker']['identity'], city_id=CITY,
                food_yield=g['food_yield'], food_surplus=g['food_surplus'],
                effective_growth_food=g['derived']['growth_food_per_turn'],
                turns_until_growth=g['turns_until_growth'],
                production_turns=phase['bomber']['turns'],
                current_production_cost=phase['bomber']['cost'],
                production_progress=phase['bomber']['production_progress'],
                city_list_display=phase['city_list'][CITY]['yields'],
                favored_yields=[k for k, v in phase['worker']['city']['yield_preferences'].items() if v['favored']],
                evidence={k: ref(phase[k]) for k in ('workforce', 'growth', 'listing', 'production')})


def focus_analysis(calls):
    action = latest(calls, 'set_city_focus', city_id=CITY, focus='food')
    reload = latest(calls, 'reload_game_save', save_name=BASE200)
    original = latest([c for c in calls if c['request'] < action['request']], 'load_game_save', save_name='clover_faith_resume_t200')
    next_mutation = next(c['request'] for c in after(calls, reload['request'])
                         if c['tool'] in ('skip_remaining_units', 'end_turn'))
    phases = [focus_phase(after(calls, original['request'], action['request'])),
              focus_phase(after(calls, action['request'], reload['request'])),
              focus_phase(after(calls, reload['request'], next_mutation))]
    a, b, restored = phases
    identities = [p['worker']['identity'] for p in phases] + [p['growth']['data']['identity'] for p in phases]
    control_keys = ('population', 'owner', 'housing', 'amenities', 'amenities_needed', 'food_stored',
                    'growth_threshold', 'housing_sources', 'amenity_sources', 'amenity_losses',
                    'housing_growth_multiplier', 'overall_growth_multiplier')
    grid_keys = ('plot_index', 'x', 'y', 'max_citizens', 'locked_citizens', 'owner_id', 'purchased_by_city', 'visible_now', 'yields')
    grids = [[{k: row[k] for k in grid_keys} for row in p['worker']['plots']] for p in phases]
    checks = [check('same_complete_identity_and_T200', all(i == identities[0] for i in identities) and identities[0]['turn'] == 200),
              check('all_native_scans_complete', all(p['worker']['scan_complete'] is True and not p['worker']['unknown_fields'] for p in phases)),
              check('focus_action_native_confirmed', not action['is_error'] and 'Native Citizens confirmed.' in action['text']),
              check('focus_changed_only_to_food', focus_metrics(a)['favored_yields'] == ['YIELD_PRODUCTION'] and focus_metrics(b)['favored_yields'] == ['YIELD_FOOD']),
              check('population_food_stock_housing_amenity_controls_fixed', all(all(p['growth_city'].get(k) == a['growth_city'].get(k) for k in control_keys) for p in phases)),
              check('native_plot_scope_and_yields_fixed', all(g == grids[0] for g in grids)),
              check('all_city_population_buildings_districts_fixed', all(list_controls(p['city_list']) == list_controls(a['city_list']) for p in phases)),
              check('granary_present_all_three', all('GRANARY' in p['city_list'][CITY]['buildings'] for p in phases)),
              check('production_item_cost_and_progress_fixed', all(all(p['bomber'][k] == a['bomber'][k] for k in ('item_name', 'cost', 'base_cost', 'production_progress', 'can_produce')) for p in phases)),
              check('A_restored_workforce_exact', a['worker'] == restored['worker']),
              check('A_restored_growth_exact', a['growth_city'] == restored['growth_city']),
              check('A_restored_production_exact', a['bomber'] == restored['bomber']),
              check('A_restored_all_city_list_exact', a['listing']['text'] == restored['listing']['text'])]
    trace = mutation_trace(calls, a['listing']['request'], next_mutation)
    checks.append(check('only_focus_then_reload_gameplay_mutations', [x['tool'] for x in trace] == ['set_city_focus', 'reload_game_save'], trace))
    shifts = []
    bplots = {r['plot_index']: r for r in b['worker']['plots']}
    for row in a['worker']['plots']:
        other = bplots.get(row['plot_index'])
        if other and row['citizens'] != other['citizens']:
            shifts.append(dict(plot_index=row['plot_index'], x=row['x'], y=row['y'],
                               citizens_A=row['citizens'], citizens_B=other['citizens'], plot_yields=row['yields']))
    return dict(status=result_status(checks), checks=checks,
                phases={name: focus_metrics(p) for name, p in zip(('A', 'B', 'A_restored'), phases)},
                changed_native_worker_rows=shifts,
                policy_control=dict(before=ref(latest(after(calls, original['request'], action['request']), 'get_policies')),
                                    limitation='Policy slots were read before focus; no policy write occurs in the recorded experiment, but B has no independent policy-slot reread.'),
                additional_controls=[dict(name='independent_policy_slot_reread_under_B', status='UNKNOWN',
                                          reason='No such readback; trace proves no submitted policy change in this experiment.')],
                limitations=['Production and science totals here are integer city-list displays, not exact native total yields.',
                             'Plot yields describe the current plot, not marginal city yields or per-specialist benefit.',
                             'No future turns were played under food focus; growth/completion turns are native estimates.',
                             'This is one city at T200; the result does not establish a generally superior focus.'])


def policy_phase(calls):
    selected = {name: latest(calls, tool) for name, tool in (
        ('yields', 'get_city_yields'), ('growth', 'get_city_growth'), ('listing', 'get_cities'),
        ('policies', 'get_policies'), ('governors', 'get_governors'))}
    selected['workforce'] = {c['args']['city_id']: c for c in find(calls, 'get_city_workforce', view='structured')}
    selected['slots'] = slots(selected['policies']['text'])
    return selected


def balance(calls):
    for call in reversed(find(calls, 'get_city_production', view='structured')):
        for option in call['data']['options']:
            for quote in option['purchases']:
                if quote['currency'] == 'YIELD_GOLD' and quote['balance'] is not None:
                    return dict(value=quote['balance'], evidence=ref(call))
    raise ValueError('No exact native gold balance readback.')


def policy_analysis(calls):
    action = latest(calls, 'set_policies', assignments='6=POLICY_PUBLIC_WORKS')
    reload = latest(calls, 'reload_game_save', save_name=BASE201)
    baseline = latest([c for c in calls if c['request'] < action['request']], 'save_game', save_name=BASE201)
    turn = latest([c for c in calls if c['request'] < baseline['request']], 'end_turn')
    windows = [after(calls, turn['request'], action['request']), after(calls, action['request'], reload['request']), after(calls, reload['request'])]
    phases = [policy_phase(w) for w in windows]
    a, b, restored = phases
    phase_data = [p['yields']['data'] for p in phases]
    identities = [d['identity'] for d in phase_data] + [p['growth']['data']['identity'] for p in phases]
    expected = deepcopy(a['slots'])
    expected[6]['policy'] = 'POLICY_PUBLIC_WORKS'
    ids = set(cities(phase_data[0]))
    checks = [check('same_complete_identity_and_T201', all(i == identities[0] for i in identities) and identities[0]['turn'] == 201),
              check('all_native_city_yield_scans_complete', all(d['status'] == 'ready' and d['scan_complete'] is True and d['native_fields_complete'] is True and not d['errors'] for d in phase_data)),
              check('policy_action_native_confirmed', not action['is_error'] and 'POLICIES_SET|' in action['text']),
              check('only_slot6_replaced', a['slots'].get(6, {}).get('policy') == 'POLICY_RATIONALISM' and b['slots'] == expected),
              check('A_policy_plan_restored_exact', restored['slots'] == a['slots']),
              check('same_five_city_population_district_building_controls', len(ids) == 5 and all(science_free(d['cities']) == science_free(phase_data[0]['cities']) for d in phase_data)),
              check('noncampus_building_science_fixed', all(noncampus_science(d) == noncampus_science(phase_data[0]) for d in phase_data)),
              check('same_non_science_growth_sources_tooltips', all(growth_controls(p['growth']['data']) == growth_controls(a['growth']['data']) for p in phases)),
              check('same_all_city_population_building_district_list', all(list_controls(city_list(p['listing']['text'])) == list_controls(city_list(a['listing']['text'])) for p in phases)),
              check('same_governor_assignments_and_establishment', all(p['governors']['text'] == a['governors']['text'] for p in phases)),
              check('workforce_covers_all_five_cities', all(set(p['workforce']) == ids for p in phases)),
              check('workforce_fixed_all_five_cities', all({i: c['data'] for i, c in p['workforce'].items()} == {i: c['data'] for i, c in a['workforce'].items()} for p in phases)),
              check('A_city_data_restored_exact', phase_data[0]['cities'] == phase_data[2]['cities']),
              check('A_yield_response_restored_except_disclosed_requirement_instance_handles', stable_engine_snapshot(phase_data[0]) == stable_engine_snapshot(phase_data[2])),
              check('A_growth_restored_exact', a['growth']['data'] == restored['growth']['data'])]
    trace = mutation_trace(calls, min(c['request'] for c in (a['yields'], a['growth'], a['listing'])),
                           max(p['yields']['request'] for p in phases) + 1)
    checks.append(check('only_policy_then_reload_no_turn_advance', [x['tool'] for x in trace] == ['set_policies', 'reload_game_save'], trace))
    rows = []
    for ident, city in cities(phase_data[0]).items():
        triplet = [cities(d)[ident] for d in phase_data]
        rows.append(dict(city_id=ident, name=city['name'], population=city['population'],
                         science_A=triplet[0]['science'], science_B=triplet[1]['science'], science_A_restored=triplet[2]['science'],
                         science_delta_B_minus_A=triplet[1]['science']-triplet[0]['science'],
                         campus_science=[{b['building_type']: b['yields']['YIELD_SCIENCE'] for b in c['campus_buildings']} for c in triplet],
                         A_native_conditions=city['policy_conditions']))
    initial_cost = re.search(r'Policy change cost: (\d+) GOLD; balance: ([\d.]+)', a['policies']['text'])
    restored_gold, b_gold = balance(windows[2]), balance(windows[1])
    observed_cost = restored_gold['value'] - b_gold['value']
    checks.append(check('quoted_cost_equals_exact_restored_A_minus_B_balance', initial_cost is not None and observed_cost == float(initial_cost[1]),
                        dict(quoted_cost=float(initial_cost[1]) if initial_cost else None, observed_cost=observed_cost)))
    return dict(status=result_status(checks), checks=checks, identity=identities[0], cities=rows,
                native_city_science_sums=[sum(c['science'] for c in d['cities']) for d in phase_data],
                A_restore_raw_response_equal=phase_data[0] == phase_data[2],
                A_restore_requirement_instance_handle_changes=requirement_handle_changes(phase_data[0], phase_data[2]),
                additional_controls=[dict(name='independent_native_power_state', status='UNKNOWN',
                                          reason='No direct power-state field in the selected city-yield interface.'),
                                     dict(name='independently_expanded_governor_promotion_tree', status='UNKNOWN',
                                          reason='Governor reads show assignment and establishment, without a full promotion-tree expansion.')],
                policy_change=dict(action=ref(action), baseline_save=ref(baseline), reload=ref(reload),
                                   exact_gold_A_restored=restored_gold, exact_gold_B=b_gold, exact_cost=observed_cost,
                                   initial_A_displayed_balance=float(initial_cost[2]) if initial_cost else None,
                                   balance_boundary='Exact A balance was measured after reloading the saved A baseline, not before the B submission.'),
                phase_evidence={name: {k: [ref(c) for c in p[k].values()] if k == 'workforce' else ref(p[k])
                                      for k in ('yields', 'growth', 'listing', 'policies', 'governors', 'workforce')}
                                for name, p in zip(('A', 'B', 'A_restored'), phases)},
                limitations=['Native city-yield responses have no independent power-state field; equal non-science building yields/tooltips are indirect controls, not a direct power getter.',
                             'Governor evidence proves assigned city and establishment as displayed, not a separately expanded promotion tree.',
                             'This same-turn intervention measures the combined replacement Rationalism -> Public Works; it does not prove a universal policy marginal value.',
                             'Native city science sums are not assumed to equal the player-wide science rate.',
                             'No T200 -> T201 change is included in this policy contrast; no subsequent gameplay outcome was measured.',
                             'The recorded controls and exact restoration do not establish equality of every unobserved world-state field.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', default='evidence/live-v4')
    parser.add_argument('--output')
    args = parser.parse_args()
    calls, source = load(resolve(args.live))
    result = dict(schema_version=1, source=source, analysis_kind='fixed same-turn A/B/A observations; read-only')
    for name, analyze in (('food_focus_T200', focus_analysis), ('rationalism_replacement_T201', policy_analysis)):
        try:
            result[name] = analyze(calls)
        except (KeyError, ValueError, StopIteration, TypeError) as exc:
            result[name] = dict(status='UNKNOWN', error=f'{type(exc).__name__}: {exc}')
    states = [result[k]['status'] for k in ('food_focus_T200', 'rationalism_replacement_T201')]
    result['status'] = ('FAIL' if 'FAIL' in states else
                        'PASS' if not source['parse_errors'] and not source['pending_tool_requests'] and states == ['PASS', 'PASS']
                        else 'UNKNOWN')
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        if not source['clean_session_close'] or result['status'] != 'PASS':
            raise SystemExit('Refusing final output: session is not cleanly closed or observations/checks are incomplete. Inspect stdout without --output.')
        resolve(args.output).write_text(serialized)
    print(serialized, end='')


if __name__ == '__main__':
    main()
