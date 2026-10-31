#!/usr/bin/env python3
"""Read-only acceptance analysis of named CLI evidence; never contacts Civ6.

Run after the controller closes the chosen session, for example:
  uv run --locked python changes/analyze_live.py --live evidence/live-v4 \
      --output evidence/live-analysis.json

Paths are relative to this run directory unless already absolute. No default
output file is written. A previous/failed live directory is never merged in.
The checks compare observations, not strategy quality or future game outcomes.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re


RUN = Path(__file__).resolve().parents[1]
YIELDS = {f'YIELD_{x}' for x in ('FOOD', 'PRODUCTION', 'GOLD', 'SCIENCE', 'CULTURE', 'FAITH')}
STAGES = (50, 100, 150, 200)
CLASSES = {'B': 'RESOURCECLASS_BONUS', 'L': 'RESOURCECLASS_LUXURY',
           'S': 'RESOURCECLASS_STRATEGIC', 'A': 'RESOURCECLASS_ARTIFACT'}
ACTORS = {'B': 'UNIT_BUILDER', 'A': 'UNIT_ARCHAEOLOGIST'}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value):
    return finite(value) and value == int(value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def status(items):
    states = [x.get('status', 'UNKNOWN') for x in items]
    return 'FAIL' if 'FAIL' in states else ('UNKNOWN' if not states or 'UNKNOWN' in states else 'PASS')


def check(name, condition, detail=None, *, unknown=False):
    out = dict(name=name, status='UNKNOWN' if unknown else ('PASS' if condition else 'FAIL'))
    if detail is not None:
        out['detail'] = detail
    return out


def payload(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def text_of(event):
    return '\n'.join(c.get('text', '') for c in event.get('result', {}).get('content', []) if c.get('type') == 'text')


def identity_valid(identity):
    return (isinstance(identity, dict) and all(integer(identity.get(k)) for k in ('turn', 'player_id', 'seed'))
            and identity['turn'] >= 0 and identity['player_id'] >= 0
            and isinstance(identity.get('civilization'), str) and identity['civilization'].startswith('CIVILIZATION_'))


def same_identity(a, b):
    return identity_valid(a) and identity_valid(b) and all(a[k] == b[k] for k in ('turn', 'player_id', 'seed', 'civilization'))


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else RUN / path


def load_evidence(live):
    candidates = [live / 'events.jsonl', live / 'debug.jsonl']
    event_file = next((p for p in candidates if p.exists()), None)
    if event_file is None:
        return [], [], [], dict(status='UNKNOWN', error='No events.jsonl or debug.jsonl in selected directory.', live=str(live))
    raw = event_file.read_bytes()
    events, errors = [], []
    for index, row in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(row)
            if not isinstance(event, dict):
                raise ValueError('event is not an object')
            events.append(event)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(dict(line=index, error=str(exc)))
    source_file = RUN / 'evidence/source-before.json'
    sources = json.loads(source_file.read_text()) if source_file.exists() else []
    by_name = {s['name']: s for s in sources}
    calls, starts, groups = [], {}, []
    current, command = None, None
    for event in events:
        kind = event.get('event')
        if kind == 'command':
            command = event.get('input')
        elif kind == 'tool_started':
            tool, args = event.get('tool'), event.get('arguments', {})
            if tool in ('load_game_save', 'reload_game_save', 'recover_game'):
                current = dict(index=len(groups), load_request=event['sequence'], arguments=args,
                               source=by_name.get(args.get('save_name')), overview_reads=[])
                groups.append(current)
            starts[event['sequence']] = (event, current, command)
        elif kind == 'tool_result':
            start = starts.pop(event.get('request'), None)
            if start is None:
                errors.append(dict(sequence=event.get('sequence'), error='Result without matching tool_started.'))
                continue
            first, group, cmd = start
            text = text_of(event)
            data = payload(text)
            identity = data.get('identity') if data else None
            scope = group.get('current_identity') if group else None
            if identity_valid(identity):
                scope = identity
                if group is not None:
                    group['current_identity'] = identity
            if first.get('tool') == 'get_game_overview' and not event.get('is_error'):
                turn = re.search(r'^Turn (\d+)(?:/\d+)? \|', text)
                civ = re.search(r'civilization_type=(CIVILIZATION_[A-Z0-9_]+)', text)
                if turn and civ and group is not None:
                    witness = dict(request=first['sequence'], turn=int(turn[1]), civilization=civ[1])
                    group['overview_reads'].append(witness)
                    previous = group.get('current_identity')
                    if previous:
                        scope = dict(previous, turn=int(turn[1]), civilization=civ[1])
                    else:
                        # Overview proves turn/civilization, not seed or player ID.
                        scope = dict(turn=int(turn[1]), civilization=civ[1])
                    group['current_identity'] = scope
            call = dict(request=first['sequence'], sequence=event.get('sequence'), tool=first.get('tool'),
                        args=first.get('arguments', {}), command=cmd, group=group['index'] if group else None,
                        identity=scope, turn=scope.get('turn') if scope else None, data=data, text=text,
                        text_utf8_bytes=len(text.encode('utf-8')), is_error=bool(event.get('is_error') or event.get('protocol_is_error')))
            try:
                call['rpc_seconds'] = (datetime.fromisoformat(event['timestamp']) - datetime.fromisoformat(first['timestamp'])).total_seconds()
            except (KeyError, ValueError):
                call['rpc_seconds'] = None
            calls.append(call)
            if group is not None and first.get('tool') in ('load_game_save', 'reload_game_save', 'recover_game'):
                group['load_result'] = dict(request=call['request'], text=text, is_error=call['is_error'])
            if first.get('tool') in ('return_to_main_menu', 'stop_game'):
                current = None
            elif first.get('tool') == 'end_turn' and group is not None:
                group.pop('current_identity', None)
    summary_file = live / 'summary.json'
    summary = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    pending = summary.get('data', {}).get('runtime', {}).get('has_pending')
    closed = summary.get('data', {}).get('mcp_session_closed') is True
    ingestion = dict(status='PASS' if not errors and not starts else 'UNKNOWN', file=str(event_file),
                     sha256=digest(raw), bytes=len(raw), event_count=len(events), parse_errors=errors,
                     pending_tool_requests=sorted(starts), run_ids=sorted({e['run_id'] for e in events if e.get('run_id')}),
                     session_closed=closed, pending_on_exit=pending,
                     clean_session_close=closed and pending is False and summary.get('data', {}).get('unresolved_on_exit') is False,
                     source_registration_file=str(source_file), source_registration_sha256=digest(source_file.read_bytes()) if source_file.exists() else None)
    return calls, groups, events, ingestion


def brief_ref(call):
    return {key: call.get(key) for key in ('request', 'command', 'tool', 'text_utf8_bytes', 'rpc_seconds')}


def source_checks(group, turn):
    source, result = group.get('source'), group.get('load_result')
    if not source or not result:
        return [check('trusted_source_load', False, 'Missing source registration or load result.', unknown=True)]
    reg, args = source['registration'], group['arguments']
    loaded = re.search(r'^Loaded (.+?): (\w+), seed (-?\d+), turn (\d+)\.', result['text'])
    checks = [check('registered_source_arguments', args.get('expected_sha256') == source['sha256']
                    and args.get('source_run_id') == reg['run_id'] and reg['turn'] == turn,
                    dict(arguments=args, registration=reg)),
              check('native_load_readback', bool(loaded) and not result['is_error']
                    and loaded[1] == source['name'] and int(loaded[3]) == reg['seed'] and int(loaded[4]) == turn,
                    result)]
    fresh = [x for x in group.get('overview_reads', []) if x['turn'] == turn]
    checks.append(check('fresh_overview_at_stage', bool(fresh) and all(x['civilization'] == 'CIVILIZATION_' + reg['civilization'].upper() for x in fresh), fresh, unknown=not fresh))
    return checks


def identity_check(call, group, turn):
    ident, src = call.get('identity'), group.get('source')
    if not src or not identity_valid(ident):
        return check('complete_current_identity', False, ident, unknown=True)
    reg = src['registration']
    return check('stage_identity', ident['turn'] == turn and ident['seed'] == reg['seed']
                 and ident['civilization'] == 'CIVILIZATION_' + reg['civilization'].upper(), ident)


def no_fact_mutations_between(calls, a, b):
    low, high = sorted((a['request'], b['request']))
    return [brief_ref(c) for c in calls if low < c['request'] < high
            and not c['tool'].startswith(('get_', 'list_', 'search_', 'check_', 'confirm_'))
            and c['tool'] not in ('save_game',)]


def parse_gp(text):
    meta = re.search(r'^Current Great People timeline: native total (\d+); matched (\d+); shown (\d+); scan complete (yes|unknown)\.', text)
    shared = {}
    line = re.search(r'^Shared balances in this response: (.+)\.$', text, re.M)
    if line:
        for part in line[1].split('; '):
            key, value = part.split(' ', 1)
            shared[key] = value
    exhausted = re.search(r'^Exhausted native classes \((\d+)\): (.+)\. No current individual', text, re.M)
    records = {}
    for block in re.findall(r'^  \S[^\n]*(?:\n(?:    [^\n]*))*', text, re.M):
        ident = re.search(r'\(individual_id: (\d+); class_type: (GREAT_PERSON_CLASS_[A-Z0-9_]+)\)', block)
        if not ident:
            continue
        def normalize_quote(match):
            currency, cost, explicit = match[1], match[2], match[3]
            balance = explicit if explicit is not None else shared.get(currency, 'MISSING_BALANCE')
            return f'{currency} {cost}; balance {balance}; can patronize'
        normalized = re.sub(r'\b(gold|faith) ([^;\n]+)(?:; balance ([^;\n]+))?; can patronize', normalize_quote, block)
        records[int(ident[1])] = dict(class_type=ident[2], facts=normalized)
    return dict(native_total=int(meta[1]) if meta else None, matched=int(meta[2]) if meta else None,
                shown=int(meta[3]) if meta else None, complete=bool(meta and meta[4] == 'yes'), people=records,
                shared_balances=shared, exhausted_count=int(exhausted[1]) if exhausted else 0,
                exhausted_classes=exhausted[2].split(', ') if exhausted else [],
                no_match_explicit='No current timeline candidate matches this filter.' in text)


def analyze_gp(calls, group, turn):
    selected = [c for c in calls if c['tool'] == 'get_great_people']
    fulls = [c for c in selected if c['args'].get('individual_id') is None and c['args'].get('class_type') is None]
    comparisons, coverage = [], set()
    for call in selected:
        native = parse_gp(call['text'])
        if call in fulls:
            continue
        previous = [f for f in fulls if f['request'] < call['request']]
        baseline = previous[-1] if previous else None
        checks = [identity_check(call, group, turn)]
        if baseline is None:
            comparisons.append(dict(filtered=brief_ref(call), status='UNKNOWN', reason='No preceding same-stage full GP observation.'))
            continue
        full = parse_gp(baseline['text'])
        ident, cls = call['args'].get('individual_id'), call['args'].get('class_type')
        expected = {i: p for i, p in full['people'].items() if (ident is None or i == ident) and (cls is None or p['class_type'] == cls)}
        role = 'and' if ident is not None and cls is not None else ('individual' if ident is not None else 'class')
        coverage.add(role)
        if not expected:
            coverage.add('no_match')
        mutations = no_fact_mutations_between(calls, baseline, call)
        checks += [check('native_scans_complete', full['complete'] and native['complete']),
                   check('same_turn_and_no_fact_mutation', baseline['turn'] == call['turn'] == turn and not mutations, mutations, unknown=bool(mutations)),
                   check('timeline_and_exhausted_classes_unchanged', all(full[k] == native[k] for k in ('native_total', 'exhausted_count', 'exhausted_classes'))),
                   check('exact_filtered_people_and_all_facts', expected == native['people'], dict(expected_ids=list(expected), actual_ids=list(native['people']))),
                   check('matching_counts', native['matched'] == native['shown'] == len(expected))]
        if not expected:
            checks.append(check('complete_no_match_is_explicit', native['no_match_explicit']))
        comparisons.append(dict(kind=role, filters=call['args'], full=brief_ref(baseline), filtered=brief_ref(call),
                                status=status(checks), checks=checks, expected_ids=list(expected),
                                utf8_saved=baseline['text_utf8_bytes'] - call['text_utf8_bytes'],
                                reduction_fraction=1 - call['text_utf8_bytes'] / baseline['text_utf8_bytes']))
    full_checks = []
    for call in fulls:
        data = parse_gp(call['text'])
        scope_check = identity_check(call, group, turn)
        full_checks.append(dict(**brief_ref(call), status=status([scope_check, check('complete_full_scan', data['complete'] and data['shown'] == len(data['people']) == data['matched'], unknown=not data['complete'])]),
                                people=len(data['people']), native_total=data['native_total'], exhausted_classes=data['exhausted_classes'],
                                shared_balances=data['shared_balances'], scope_check=scope_check))
    required = {'individual', 'class', 'and', 'no_match'}
    checks = [check('full_query_present', bool(fulls), unknown=not fulls),
              check('filter_coverage', required <= coverage, dict(observed=sorted(coverage), missing=sorted(required-coverage)), unknown=not required <= coverage)]
    checks += [check('full_scans', all(c['status'] == 'PASS' for c in full_checks), full_checks, unknown=not full_checks)]
    return dict(status=status(checks + comparisons), checks=checks, full_queries=full_checks, comparisons=comparisons,
                boundary='Exact equality applies to the requested current subset, not the omitted people or a future decision outcome.')


def scalar(text):
    if text in ('?', 'unknown'): return None
    if text in ('yes', 'no'): return text == 'yes'
    if re.fullmatch(r'-?\d+', text): return int(text)
    return text


def native_number(text):
    if text in ('unknown', '?'):
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Nonfinite displayed number.')
    return int(value) if value.is_integer() else value


def decode_search_brief(text):
    scopes, rows = {}, []
    for line in text.splitlines():
        scope = re.fullmatch(r'Row scope S(\d+): (.*)', line)
        if scope:
            scopes[int(scope[1])] = None if scope[2] == 'unknown' else scope[2]
        if not line.startswith('('):
            continue
        prefix, sep, details = line.partition('; ')
        loc = re.fullmatch(r'\((-?\d+),(-?\d+)\) (\S+) (.*?)(?: \[([^\]]+)\])?', prefix)
        if not sep or not loc:
            raise ValueError('Unparseable resource row: ' + line)
        row = dict(x=int(loc[1]), y=int(loc[2]), type=loc[3], name=loc[4])
        if loc[5] is not None:
            row['class'] = CLASSES.get(loc[5], scalar(loc[5]))
        labels = dict(owner='owner_id', visible='visible_now', facility='improvement_type', pillaged='pillaged', actor='work_unit', details='details_status')
        extra = details.find('extra=')
        if extra >= 0:
            row.update(json.loads(details[extra+6:])); details = details[:extra].rstrip('; ')
        selected_scope = 1
        for part in details.split('; ') if details else []:
            key, _, val = part.partition('=')
            if key == 'scope':
                selected_scope = int(val.removeprefix('S'))
            elif key in labels:
                row[labels[key]] = ACTORS.get(val, scalar(val)) if key == 'actor' else scalar(val)
            else:
                raise ValueError('Unknown resource row label: ' + key)
        if selected_scope not in scopes:
            raise ValueError('Missing shared row scope.')
        row['scope'] = scopes[selected_scope]
        rows.append(row)
    return rows


def search_key(call):
    args = call['args']
    return tuple(args.get(k, default) for k, default in (('search_kind', 'resource'), ('query', ''), ('limit', 50), ('offset', 0)))


def analyze_map(calls, group, turn):
    selected = [c for c in calls if c['tool'] == 'get_public_report' and c['args'].get('section') == 'map-search']
    comparisons = []
    for short in [c for c in selected if c['args'].get('view') == 'brief']:
        options = [c for c in selected if c['args'].get('view', 'structured') == 'structured' and search_key(c) == search_key(short)]
        if not options:
            comparisons.append(dict(brief=brief_ref(short), status='UNKNOWN', reason='No same-scope structured expansion.'))
            continue
        long = min(options, key=lambda c: abs(c['request'] - short['request']))
        b, s = short['data'], long['data']
        if not b or not s:
            comparisons.append(dict(brief=brief_ref(short), structured=brief_ref(long), status='UNKNOWN', reason='Non-JSON or failed report.'))
            continue
        rows = (s.get('data') or {}).get('locations')
        try:
            decoded = decode_search_brief(b.get('report', ''))
            decode_error = None
        except (ValueError, TypeError, KeyError) as exc:
            decoded, decode_error = None, str(exc)
        metadata = {k: v for k, v in s.items() if k != 'data'}
        scope = (s.get('data') or {}).get('scope')
        expected_expand = dict(command='map search', search_kind=search_key(short)[0], query=search_key(short)[1],
                               limit=search_key(short)[2], offset=search_key(short)[3], view='structured')
        extra = {k: v for k, v in (s.get('data') or {}).items() if k not in ('scope', 'locations')}
        mutations = no_fact_mutations_between(calls, short, long)
        checks = [identity_check(short, group, turn), identity_check(long, group, turn),
                  check('same_complete_identity', same_identity(b.get('identity'), s.get('identity'))),
                  check('no_fact_mutation_between_views', not mutations, mutations, unknown=bool(mutations)),
                  check('all_structured_metadata_retained', all(b.get(k) == v and k in b for k, v in metadata.items())),
                  check('same_data_scope_and_extensions', b.get('scope') == scope and b.get('data_extra', {}) == extra),
                  check('exact_expansion_arguments', b.get('expand') == expected_expand, b.get('expand')),
                  check('every_row_field_recovered', rows is not None and decoded == rows, dict(decode_error=decode_error, source_rows=len(rows) if isinstance(rows, list) else None, decoded_rows=len(decoded) if decoded is not None else None), unknown=rows is None),
                  check('shown_count', b.get('shown') == (len(rows) if isinstance(rows, list) else None))]
        comparisons.append(dict(status=status(checks), brief=brief_ref(short), structured=brief_ref(long), checks=checks,
                                rows=len(rows) if isinstance(rows, list) else None,
                                unique_row_scopes=len({x.get('scope') for x in rows}) if rows is not None else None,
                                brief_report_utf8_bytes=len(b.get('report', '').encode('utf-8')),
                                utf8_saved=long['text_utf8_bytes'] - short['text_utf8_bytes'],
                                reduction_fraction=1 - short['text_utf8_bytes'] / long['text_utf8_bytes']))
    return dict(status=status(comparisons), comparisons=comparisons)


def analyze_workforce(calls, group, turn):
    observations = []
    for call in [c for c in calls if c['tool'] == 'get_city_workforce' and c['args'].get('view', 'brief') != 'brief']:
        data = call['data'] or {}
        checks = [identity_check(call, group, turn)]
        rows, city = data.get('plots'), data.get('city') or {}
        complete = data.get('scan_complete') is True and isinstance(rows, list)
        checks.append(check('native_manage_scan_available', complete, dict(native_status=data.get('status'), error=data.get('error'), unknown_fields=data.get('unknown_fields')), unknown=not complete))
        details = dict(city_id=city.get('city_id'), name=city.get('name'), native_status=data.get('status'),
                       scan_complete=data.get('scan_complete'), unknown_fields=data.get('unknown_fields'), rows=None)
        if complete:
            checks += [check('requested_city_binding', city.get('city_id') == call['args'].get('city_id') and city.get('owner') == data['identity']['player_id']),
                       check('six_yield_preferences', set(city.get('yield_preferences', {})) == YIELDS and all(set(v) == {'favored', 'ignored'} and all(x is None or type(x) is bool for x in v.values()) for v in city.get('yield_preferences', {}).values())),
                       check('unique_native_keys_and_plots', len({r.get('native_key') for r in rows}) == len(rows) == len({r.get('plot_index') for r in rows})),
                       check('native_nonnegative_worker_counts', all(all(integer(r.get(k)) and r[k] >= 0 for k in ('citizens', 'max_citizens', 'locked_citizens')) for r in rows)),
                       check('six_explicit_yields', all(set(r.get('yields', {})) == YIELDS and all(v is None or finite(v) for v in r['yields'].values()) for r in rows))]
            if call['args'].get('view') == 'evidence':
                checks.append(check('native_key_evidence_matches_rows', data.get('native_manage_keys') == [r.get('native_key') for r in rows]))
                if data.get('purchased_plot_scan_complete'):
                    purchased = data.get('purchased_plot_ids')
                    checks.append(check('purchased_plot_binding', isinstance(purchased, list) and all(r.get('purchased_by_city') == (r['plot_index'] in purchased) for r in rows)))
            details.update(rows=len(rows), workers=sum(r['citizens'] for r in rows), locks=sum(r['locked_citizens'] for r in rows),
                           unknown_yields=sum(v is None for r in rows for v in r['yields'].values()),
                           zero_yields=sum(v == 0 for r in rows for v in r['yields'].values()),
                           worked_count_disagreements=[dict(plot_index=r['plot_index'], citizens=r['citizens'], is_plot_worked=r.get('is_plot_worked')) for r in rows if type(r.get('is_plot_worked')) is bool and r['is_plot_worked'] != (r['citizens'] > 0)],
                           food_surplus=city.get('food_surplus'), population=city.get('population'))
        observations.append(dict(**brief_ref(call), **details, status=status(checks), checks=checks))
    comparisons = compare_workforce_briefs(calls)
    return dict(status=status(observations + comparisons), observations=observations, brief_comparisons=comparisons,
                boundary='Native counts are not assumed ordered: a center may have citizens=1/max_citizens=0. Worked/count disagreements are reported as observations; no population, idle-citizen, or free-citizen estimate is inferred.')


def decode_workforce_brief(text):
    header = re.search(r'^City workforce: (\w+) \| (.+)$', text, re.M)
    city = re.search(r'^(.+) \[id:(\d+)\] pop ([^;]+); food surplus (.+)\.$', text, re.M)
    focus = re.search(r'^  Focus: favored (.*); ignored (.*); others neither; unknown (.*)\.$', text, re.M)
    count = re.search(r'^Native MANAGE rows: (\w+);', text, re.M)
    if not all((header, city, focus, count)):
        raise ValueError('Incomplete workforce brief header.')
    rows = []
    for line in text.splitlines():
        if not line.startswith('  ('):
            continue
        match = re.fullmatch(r'  \(([^,]+),([^\)]+)\) \[plot:(\d+)\] citizens ([^/]+)/([^;]+); locked ([^;]+); worked ([^;]+); visible now ([^;]+); owner ([^;]+); assigned_here ([^;]+); (.*); other yields 0\.', line)
        if not match:
            raise ValueError('Unparseable workforce plot row: ' + line)
        yields = {k: 0 for k in YIELDS}
        if match[11] != 'all six yields 0':
            for field in match[11].split(','):
                key, sep, value = field.partition('=')
                if not sep or 'YIELD_' + key not in YIELDS:
                    raise ValueError('Unknown workforce yield field: ' + field)
                yields['YIELD_' + key] = native_number(value)
        rows.append(dict(x=native_number(match[1]), y=native_number(match[2]), plot_index=int(match[3]),
                         citizens=native_number(match[4]), max_citizens=native_number(match[5]), locked_citizens=native_number(match[6]),
                         is_plot_worked=scalar(match[7]), visible_now=scalar(match[8]), owner_id=native_number(match[9]), purchased_by_city=scalar(match[10]), yields=yields))
    def names(value):
        return set() if value == 'none' else set(value.split(','))
    return dict(status=header[1], identity=ast.literal_eval(header[2]), city_id=int(city[2]), name=city[1],
                population=native_number(city[3]), food_surplus=native_number(city[4]),
                favored=names(focus[1]), ignored=names(focus[2]), unknown_focus=names(focus[3]),
                shown=native_number(count[1]), plots=rows)


def compare_workforce_briefs(calls):
    comparisons = []
    selected = [c for c in calls if c['tool'] == 'get_city_workforce']
    for short in [c for c in selected if c['args'].get('view', 'brief') == 'brief']:
        candidates = [c for c in selected if c['args'].get('view', 'brief') != 'brief' and c['args'].get('city_id') == short['args'].get('city_id')]
        if not candidates:
            comparisons.append(dict(brief=brief_ref(short), status='UNKNOWN', reason='No same-city structured/evidence observation.'))
            continue
        long = min(candidates, key=lambda c: abs(c['request']-short['request']))
        structured = long['data'] or {}
        try:
            decoded = decode_workforce_brief((short['data'] or {}).get('report', ''))
            error = None
        except (ValueError, SyntaxError, TypeError) as exc:
            decoded, error = None, str(exc)
        if decoded is None:
            comparisons.append(dict(brief=brief_ref(short), structured=brief_ref(long), status='UNKNOWN', reason=error))
            continue
        city, plots = structured.get('city') or {}, structured.get('plots')
        fields = ('x', 'y', 'plot_index', 'citizens', 'max_citizens', 'locked_citizens', 'is_plot_worked', 'visible_now', 'owner_id', 'purchased_by_city', 'yields')
        expected = [{k: row.get(k) for k in fields} for row in plots] if isinstance(plots, list) else None
        prefs = city.get('yield_preferences', {})
        checks = [check('native_manage_available_for_comparison', structured.get('scan_complete') is True and expected is not None, unknown=not structured.get('scan_complete')),
                  check('status_and_identity_retained', decoded['status'] == structured.get('status') and same_identity(decoded['identity'], structured.get('identity'))),
                  check('city_population_and_surplus_retained', all(decoded[k] == city.get(k) for k in ('city_id', 'name', 'population', 'food_surplus'))),
                  check('all_plot_decision_fields_and_explicit_zero_yields_retained', expected == decoded['plots'], dict(brief_rows=len(decoded['plots']), native_rows=len(expected) if expected is not None else None), unknown=expected is None),
                  check('focus_and_unknown_labels_retained', decoded['favored'] == {k for k, v in prefs.items() if v.get('favored') is True}
                        and decoded['ignored'] == {k for k, v in prefs.items() if v.get('ignored') is True}
                        and decoded['unknown_focus'] == {k for k, v in prefs.items() if v.get('favored') is None or v.get('ignored') is None}),
                  check('native_row_count_retained', decoded['shown'] == (len(expected) if expected is not None else None)),
                  check('no_fact_mutation_between_views', not no_fact_mutations_between(calls, short, long), unknown=bool(no_fact_mutations_between(calls, short, long)))]
        comparisons.append(dict(status=status(checks), brief=brief_ref(short), structured=brief_ref(long), checks=checks,
                                brief_report_utf8_bytes=len((short['data'] or {}).get('report', '').encode('utf-8')),
                                utf8_saved=long['text_utf8_bytes']-short['text_utf8_bytes'], reduction_fraction=1-short['text_utf8_bytes']/long['text_utf8_bytes'],
                                intentionally_uncompared_structured_fields=['native_key', 'native_manage_keys', 'purchased_plot_ids', 'manage_diagnostics', 'scope', 'yield_basis']))
    return comparisons


def parse_settle_blocks(text):
    starts = list(re.finditer(r'^  #\d+ \((-?\d+),(-?\d+)\):', text, re.M))
    result = []
    for i, match in enumerate(starts):
        end = starts[i+1].start() if i+1 < len(starts) else len(text)
        block = text[match.start():end].rstrip('\n')
        result.append(dict(coordinates=(int(match[1]), int(match[2])), facts=re.sub(r'^  #\d+ ', '  ', block, count=1)))
    return result


def settle_scope(call):
    args = call['args']
    return tuple(args.get(k) for k in ('center_x', 'center_y', 'radius'))


def settle_chain(scoped, limit, parsed, calls):
    """Only a continuous, stable native chain can establish a full reference."""
    candidates = [c for c in scoped if c['args'].get('limit', 20) == limit and c.get('data')]
    pages, checks = {}, []
    for call in candidates:
        offset = call['data'].get('offset')
        if not integer(offset) or offset < 0:
            checks.append(check('chain_offset_valid', False, brief_ref(call)))
            continue
        if offset in pages:
            previous = pages[offset]
            checks.append(check('repeated_offset_is_identical', previous['data'] == call['data'] and parsed[previous['request']] == parsed[call['request']],
                                dict(offset=offset, requests=[previous['request'], call['request']])))
        else:
            pages[offset] = call
    first = pages.get(0)
    if first is None:
        return dict(limit=limit, status='UNKNOWN', complete=False, requests=[], restored=0,
                    reason='No offset-zero page for this page size.', checks=checks, _rows=[], _calls=[])
    total = first['data'].get('total')
    if not integer(total) or total < 0:
        checks.append(check('chain_total_available', False, total, unknown=True))
        return dict(limit=limit, status=status(checks), complete=False, requests=[], restored=0, checks=checks, _rows=[], _calls=[])
    offset, restored, selected, visited = 0, [], [], set()
    while offset is not None:
        if offset in visited:
            checks.append(check('chain_offset_advances', False, offset)); break
        visited.add(offset)
        page = pages.get(offset)
        if page is None:
            checks.append(check('chain_next_page_present', False, dict(missing_offset=offset), unknown=True)); break
        selected.append(page)
        data, rows = page['data'], parsed[page['request']]
        end = offset + len(rows)
        truncated = end < total
        checks += [check('chain_total_scope_order_stable', all(data.get(k) == first['data'].get(k) for k in ('total', 'scope', 'scope_note', 'order'))),
                   check('chain_offset_shown_and_limit', data.get('offset') == offset and data.get('shown') == len(rows)
                         and data.get('limit') == limit and len(rows) <= limit and end <= total),
                   check('chain_native_next_offset', data.get('truncated') is truncated and data.get('next_offset') == (end if truncated else None))]
        restored.extend(rows)
        if truncated and not rows:
            checks.append(check('chain_nonterminal_page_advances', False)); break
        # Follow the independently checked expected position, not an unchecked
        # native pointer that could silently skip a gap or overlap records.
        offset = end if truncated else None
    checks.append(check('chain_unique_coordinates', len({r['coordinates'] for r in restored}) == len(restored)))
    if selected:
        mutations = no_fact_mutations_between(calls, min(selected, key=lambda c: c['request']), max(selected, key=lambda c: c['request']))
        checks.append(check('chain_no_fact_mutations', not mutations, mutations, unknown=bool(mutations)))
    terminal = offset is None and len(restored) == total
    checks.append(check('chain_complete_native_termination', terminal, dict(ended_at_offset=offset, restored=len(restored), total=total), unknown=not terminal))
    complete = terminal and status(checks) == 'PASS'
    return dict(limit=limit, status=status(checks), complete=complete, requests=[c['request'] for c in selected],
                restored=len(restored), total=total, ended_at_offset=offset, checks=checks,
                body_utf8_bytes=sum(c['text_utf8_bytes'] for c in selected),
                facts_sha256=digest(json.dumps(restored, ensure_ascii=False, separators=(',', ':')).encode('utf-8')),
                _rows=restored, _calls=selected)


def chain_public(chain):
    return {key: value for key, value in chain.items() if not key.startswith('_')}


def chain_ref(chain):
    calls = chain['_calls']
    return dict(request=calls[0]['request'] if calls else None, requests=chain.get('requests', []),
                command=f"complete limit={chain['limit']} offset chain", tool='get_global_settle_advisor',
                text_utf8_bytes=chain.get('body_utf8_bytes'), rpc_seconds=sum(c.get('rpc_seconds') or 0 for c in calls))


def analyze_settle(calls, group, turn):
    selected = [c for c in calls if c['tool'] == 'get_global_settle_advisor']
    observations, comparisons, coverage = [], [], []
    parsed = {c['request']: parse_settle_blocks((c['data'] or {}).get('report', '')) for c in selected}
    for call in selected:
        data, rows = call['data'] or {}, parsed[call['request']]
        known = all(integer(data.get(k)) and data[k] >= 0 for k in ('total', 'shown', 'offset', 'limit', 'omitted'))
        end = data.get('offset', 0) + len(rows)
        checks = [identity_check(call, group, turn), check('pagination_metadata_available', known, unknown=not known)]
        if known:
            truncated = end < data['total']
            checks += [check('full_multiline_rows_counted', len(rows) == data['shown'] <= data['limit']),
                       check('pagination_args_and_next_offset', data['offset'] == call['args'].get('offset', 0)
                             and data['limit'] == call['args'].get('limit', 20) and data['omitted'] == data['total']-data['shown']
                             and data.get('truncated') is truncated and data.get('next_offset') == (end if truncated else None)),
                       check('unique_coordinates', len({r['coordinates'] for r in rows}) == len(rows))]
        observations.append(dict(**brief_ref(call), status=status(checks), checks=checks, scope=data.get('scope'),
                                 total=data.get('total'), shown=data.get('shown'), offset=data.get('offset'), limit=data.get('limit'),
                                 next_offset=data.get('next_offset'), dynamic_total_unknown_rows=sum('F:unknown P:unknown' in r['facts'] for r in rows),
                                 center_ownership_unknown_rows=sum('owner unknown' in r['facts'] for r in rows)))
    scopes = {settle_scope(c) for c in selected}
    fulls = {}
    for scope in scopes:
        scoped = [c for c in selected if settle_scope(c) == scope]
        limits = {c['args'].get('limit', 20) for c in scoped}
        chains = [settle_chain(scoped, limit, parsed, calls) for limit in sorted(limits)]
        reference = next((chain for chain in chains if chain['limit'] == 200 and chain['complete']), None)
        if reference is None:
            coverage.append(dict(scope=list(scope), status='FAIL' if any(c['status'] == 'FAIL' for c in chains) else 'UNKNOWN', reason='No complete, stable limit-200 native reference chain.',
                                 chains=[chain_public(c) for c in chains]))
            continue
        fulls[scope] = reference
        full_rows, total = reference['_rows'], reference['total']
        first = reference['_calls'][0]
        for call in scoped:
            if call['request'] in reference['requests']:
                continue
            data = call['data'] or {}
            offset = data.get('offset')
            expected = full_rows[offset:offset+data['limit']] if integer(offset) and integer(data.get('limit')) else None
            mutation = no_fact_mutations_between(calls, first, call)
            checks = [check('same_scope_count', data.get('total') == total and all(data.get(k) == first['data'].get(k) for k in ('scope', 'scope_note', 'order'))),
                      check('exact_candidate_multiline_facts', parsed[call['request']] == expected),
                      check('no_fact_mutations', not mutation, mutation, unknown=bool(mutation))]
            comparisons.append(dict(status=status(checks), full=chain_ref(reference), page=brief_ref(call), checks=checks,
                                    scope=list(scope), offset=offset, shown=data.get('shown'), utf8_saved=reference['body_utf8_bytes']-call['text_utf8_bytes'],
                                    reduction_fraction=1-call['text_utf8_bytes']/reference['body_utf8_bytes']))
        for chain in chains:
            chain['exact_full_candidate_set'] = chain['_rows'] == full_rows
        independent = [chain for chain in chains if chain['limit'] != 200]
        if scope == (None, None, None):
            coverage.append(dict(scope='global', status='PASS' if any(c['complete'] and c['exact_full_candidate_set'] for c in independent) else 'UNKNOWN',
                                 reference=chain_public(reference), chains=[chain_public(c) for c in chains],
                                 reason='A separate page-size chain must restore the complete limit-200 reference. Default is a prefix with disclosed omissions.'))
        else:
            coverage.append(dict(scope=list(scope), status='PASS', reference=chain_public(reference), total=total, chains=[chain_public(c) for c in chains]))
    global_full = fulls.get((None, None, None))
    subsets = []
    for scope, regional in fulls.items():
        if scope == (None, None, None):
            continue
        if global_full is None:
            subsets.append(dict(scope=list(scope), status='UNKNOWN', reason='Missing global full set.'))
            continue
        all_rows = {r['coordinates']: r for r in global_full['_rows']}
        rows = regional['_rows']
        mutation = no_fact_mutations_between(calls, global_full['_calls'][0], regional['_calls'][0])
        checks = [check('regional_candidates_preserve_global_full_facts', all(all_rows.get(r['coordinates']) == r for r in rows)),
                  check('no_fact_mutations_between_scopes', not mutation, mutation, unknown=bool(mutation))]
        subsets.append(dict(scope=list(scope), status=status(checks), checks=checks, global_full=chain_ref(global_full), region=chain_ref(regional), rows=len(rows),
                            boundary='Checks same-candidate facts; it does not replace the engine hex-distance/visibility rule with a geometric approximation.'))
    default_present = any(settle_scope(c) == (None, None, None) and c['args'].get('limit', 20) == 20 and c['args'].get('offset', 0) == 0 for c in selected)
    checks = [check('global_full_present', global_full is not None, unknown=global_full is None),
              check('default20_present', default_present, unknown=not default_present),
              check('regional_scope_present', bool(subsets), unknown=not subsets)]
    return dict(status=status(checks+observations+comparisons+coverage+subsets), checks=checks, observations=observations,
                comparisons=comparisons, pagination_coverage=coverage, regional_subsets=subsets,
                boundary='A shortened page is not the full candidate set; exact offset recovery is checked independently. Native unknown dynamic totals/ownership remain unknown.')


def analyze_growth(calls, group, turn):
    observations = []
    source_keys = {'housing_sources': {'water', 'buildings', 'improvements', 'districts', 'civics', 'great_people', 'starting_era', 'great_works'},
                   'amenity_sources': {'luxuries', 'entertainment', 'civics', 'great_people', 'city_states', 'religion', 'national_parks', 'starting_era', 'improvements', 'governors', 'districts', 'natural_wonders', 'traits'},
                   'amenity_losses': {'war_weariness', 'bankruptcy'}}
    for call in [c for c in calls if c['tool'] == 'get_city_growth' and c['args'].get('view', 'brief') != 'brief']:
        data = call['data'] or {}
        checks, facts = [identity_check(call, group, turn)], []
        cities = data.get('cities')
        checks.append(check('city_growth_observation_available', isinstance(cities, list) and bool(cities), dict(native_status=data.get('status'), unknown_fields=data.get('unknown_fields')), unknown=not cities))
        for city in cities or []:
            fact = {k: city.get(k) for k in ('city_id', 'name', 'population', 'food_surplus', 'housing', 'amenities', 'amenities_needed', 'housing_growth_multiplier')}
            if call['args'].get('view') == 'evidence':
                for name, keys in source_keys.items():
                    values = city.get(name)
                    checks.append(check(f"city.{city.get('city_id')}.{name}.complete_keys", isinstance(values, dict) and set(values) == keys))
                    values = values or {}
                    checks.append(check(f"city.{city.get('city_id')}.{name}.numeric_or_explicit_unknown", all(v is None or finite(v) for v in values.values())))
                    missing = [k for k, v in values.items() if v is None]
                    checks.append(check(f"city.{city.get('city_id')}.{name}.unknowns_labelled", all(f"city.{city['city_id']}.{name}.{k}" in data.get('unknown_fields', []) for k in missing)))
                    fact[name] = values
                    fact[name + '_known'] = len(values) - len(missing)
                values = [v for name in source_keys for v in (city.get(name) or {}).values()]
                fact.update(source_zero_count=sum(v == 0 for v in values), source_negative_values=[v for v in values if finite(v) and v < 0],
                            source_unknown_count=sum(v is None for v in values))
            d = city.get('derived', {})
            a, b = city.get('food_yield'), city.get('food_surplus')
            if finite(a) and finite(b):
                checks.append(check(f"city.{city.get('city_id')}.food_consumption", d.get('food_consumption') == a-b))
            facts.append(fact)
        observations.append(dict(**brief_ref(call), native_status=data.get('status'), status=status(checks), checks=checks,
                                 cities=facts, unknown_fields=data.get('unknown_fields', [])))
    evidence = any(c['args'].get('view') == 'evidence' for c in calls if c['tool'] == 'get_city_growth')
    checks = [check('source_components_evidence_view_present', evidence, unknown=not evidence)]
    return dict(status=status(checks + observations), checks=checks, observations=observations,
                boundary='Component sums are not required to equal engine totals; source zeros, native signs and unknown values remain separate.')


def analyze_production(calls, group, turn):
    observations = []
    for call in [c for c in calls if c['tool'] == 'get_city_production' and c['args'].get('view') == 'structured']:
        data = call['data'] or {}
        options = data.get('options')
        checks = [identity_check(call, group, turn), check('structured_options_available', isinstance(options, list), unknown=not isinstance(options, list))]
        unknown, changed, known, zeros, categories = [], [], [], [], Counter()
        for item in options or []:
            name = item.get('item_name')
            categories[item.get('category')] += 1
            checks.append(check(name + '.separate_native_and_base_fields', all(k in item for k in ('cost', 'base_cost', 'production_progress', 'turns', 'can_produce'))))
            for field in ('cost', 'base_cost', 'production_progress', 'turns'):
                val = item.get(field)
                checks.append(check(name + '.' + field + '.numeric_or_null', val is None or (finite(val) and val >= 0 and (field != 'turns' or integer(val)))))
                if val is None:
                    unknown.append(dict(item=name, field=field, can_produce=item.get('can_produce')))
                elif val == 0:
                    zeros.append(dict(item=name, field=field))
            if item.get('can_produce') is True:
                (known if all(item.get(k) is not None for k in ('cost', 'production_progress', 'turns')) else unknown).append(dict(item=name, native_production_facts={k: item.get(k) for k in ('cost', 'production_progress', 'turns')}))
            if item.get('cost') is not None and item.get('base_cost') is not None and item['cost'] != item['base_cost']:
                changed.append(dict(item=name, native_cost=item['cost'], base_cost=item['base_cost'], invested=item.get('production_progress')))
            quotes = item.get('purchases', [])
            checks.append(check(name + '.purchase_quotes_are_independent', len({q.get('currency') for q in quotes}) == len(quotes) and all(q.get('currency') in ('YIELD_GOLD', 'YIELD_FAITH') for q in quotes)))
            checks.append(check(name + '.purchase_unknowns_and_refusals', all((q.get('cost') is None or finite(q['cost']) and q['cost'] >= 0) and (q.get('can_purchase') is None or type(q['can_purchase']) is bool)
                                and (q.get('can_purchase') is not False or bool(q.get('reasons'))) for q in quotes)))
        observations.append(dict(**brief_ref(call), city_id=data.get('city_id'), status=status(checks), checks=checks,
                                 categories=dict(categories), options=len(options) if isinstance(options, list) else None,
                                 known_available_options=len(known), unknown_fields=unknown, zero_fields=zeros,
                                 native_cost_differs_from_static=changed))
    return dict(status=status(observations), observations=observations,
                boundary='Native numeric validity and separate scopes are checked. No claim about actual charge or action success is inferred from these read-only quotes.')


def query_statistics(calls):
    reads = [c for c in calls if c['tool'].startswith(('get_', 'list_', 'search_', 'check_'))]
    by_tool = {}
    for tool in sorted({c['tool'] for c in reads}):
        selected = [c for c in reads if c['tool'] == tool]
        by_tool[tool] = dict(commands=len(selected), text_utf8_bytes=sum(c['text_utf8_bytes'] for c in selected),
                             rpc_seconds=sum(c['rpc_seconds'] or 0 for c in selected))
    return dict(read_commands=len(reads), text_utf8_bytes=sum(c['text_utf8_bytes'] for c in reads),
                rpc_seconds=sum(c['rpc_seconds'] or 0 for c in reads), by_tool=by_tool,
                requests=[c['request'] for c in reads], boundary='Counts named read tool calls once; omits loads/saves/mutations and all other experiment phases.')


def source_code_check(live):
    freeze = RUN / 'evidence/code-sha-v4.json'
    context_file = live / 'context.json'
    if not freeze.exists() or not context_file.exists():
        return check('frozen_runtime_and_current_source_hashes', False, 'Missing freeze/context.', unknown=True)
    hashes = json.loads(freeze.read_text())
    context = json.loads(context_file.read_text()).get('source_sha256', {})
    project = RUN.parents[3]
    runtime_diff = [name for name, sha in hashes.items() if context.get(name) != sha]
    current_diff = [name for name, sha in hashes.items() if not (project/name).exists() or digest((project/name).read_bytes()) != sha]
    return check('frozen_runtime_and_current_source_hashes', not runtime_diff and not current_diff,
                 dict(freeze_file=str(freeze), freeze_sha256=digest(freeze.read_bytes()), files=len(hashes),
                      runtime_differences=runtime_diff, current_differences=current_diff))


def independent_segments(calls, baseline_end):
    """Keep contrast experiments outside the four historical baseline costs."""
    result = []
    food = next((c for c in calls if c['tool'] == 'set_city_focus' and c['request'] > baseline_end), None)
    transition = next((c for c in calls if c['tool'] == 'end_turn' and c['request'] > baseline_end), None)
    if food:
        end = transition['request'] if transition else math.inf
        window = [c for c in calls if baseline_end < c['request'] < end]
        city_id = food['args']['city_id']
        reload = next((c for c in window if c['tool'] == 'reload_game_save' and c['request'] > food['request']), None)
        def workforce(candidates):
            valid = [c for c in candidates if c['tool'] == 'get_city_workforce' and c['args'].get('city_id') == city_id
                     and (c.get('data') or {}).get('plots') is not None]
            return valid[-1] if valid else None
        a = workforce([c for c in calls if c['request'] <= baseline_end and c['turn'] == 200])
        b = workforce([c for c in window if food['request'] < c['request'] < (reload['request'] if reload else math.inf)])
        restored = workforce([c for c in window if reload and c['request'] > reload['request']])
        phases = [a, b, restored]
        checks = [check('food_focus_native_confirmation', food['text'].startswith('FOCUS_SET|') and not food['is_error']),
                  check('three_native_workforce_states', all(c and c['data'].get('scan_complete') is True for c in phases), unknown=not all(phases))]
        facts = []
        if all(phases):
            checks += [check('food_same_turn_identity', same_identity(a['data']['identity'], b['data']['identity']) and same_identity(a['data']['identity'], restored['data']['identity'])),
                       check('food_A_city_and_plots_restored', a['data']['city'] == restored['data']['city'] and a['data']['plots'] == restored['data']['plots'])]
            for name, call in zip(('A', 'B_food_focus', 'A_restored'), phases):
                data = call['data']; city = data['city']
                facts.append(dict(phase=name, observation=brief_ref(call), city_id=city_id, population=city['population'], food_surplus=city['food_surplus'],
                                  yield_preferences=city['yield_preferences'], workers=sum(r['citizens'] for r in data['plots'])))
        result.append(dict(name='T200_food_focus_A_B_reload_A', status=status(checks), checks=checks, phases=facts,
                           query_statistics=query_statistics(window), actions=[brief_ref(c) for c in window if c['tool'] in ('set_city_focus', 'reload_game_save')],
                           excluded_from_four_stage_baseline=True))
    policy = next((c for c in calls if c['tool'] == 'set_policies' and transition and c['request'] > transition['request']), None)
    if policy:
        window = [c for c in calls if c['request'] > transition['request']]
        reload = next((c for c in window if c['tool'] == 'reload_game_save' and c['request'] > policy['request']), None)
        def yields(candidates):
            valid = [c for c in candidates if c['tool'] == 'get_city_yields' and (c.get('data') or {}).get('cities')]
            return valid[-1] if valid else None
        a = yields([c for c in window if c['request'] < policy['request']])
        b = yields([c for c in window if policy['request'] < c['request'] < (reload['request'] if reload else math.inf)])
        restored = yields([c for c in window if reload and c['request'] > reload['request']])
        checks = [check('policy_native_confirmation', policy['text'].startswith('POLICIES_SET|') and not policy['is_error']),
                  check('three_native_yield_states', all((a, b, restored)), unknown=not all((a, b, restored)))]
        facts = []
        if all((a, b, restored)):
            phases = [a, b, restored]
            checks.append(check('policy_fixed_T201_identity', all(c['data']['identity']['turn'] == 201 and same_identity(a['data']['identity'], c['data']['identity']) for c in phases)))
            rows = [{c['city_id']: c for c in call['data']['cities']} for call in phases]
            def unchanged_inventory(row):
                # Current building science is the measured policy outcome;
                # compare inventory/pillage and all other yields separately.
                return dict(population=row.get('population'), food_surplus=row.get('food_surplus'), districts=row.get('districts'),
                            buildings=[dict(building, yields={k: v for k, v in building.get('yields', {}).items() if k != 'YIELD_SCIENCE'})
                                       for building in row.get('buildings', [])])
            checks += [check('same_city_set', set(rows[0]) == set(rows[1]) == set(rows[2])),
                       check('rationalism_native_slot_triplet', [c['data'].get('policy', {}).get('slotted') for c in phases] == [True, False, True]),
                       check('city_science_A_restored', all(row['science'] == rows[2].get(i, {}).get('science') for i, row in rows[0].items())),
                       check('population_food_inventory_pillage_and_non_science_yields_unchanged', all(unchanged_inventory(row) == unchanged_inventory(other.get(i, {})) for i, row in rows[0].items() for other in rows[1:]))]
            for city_id, row in rows[0].items():
                building_science = []
                for building in row.get('buildings', []):
                    key = building.get('building_type')
                    before = building.get('yields', {}).get('YIELD_SCIENCE')
                    alternatives = [{x.get('building_type'): x for x in other.get(city_id, {}).get('buildings', [])} for other in rows[1:]]
                    after = [x.get(key, {}).get('yields', {}).get('YIELD_SCIENCE') for x in alternatives]
                    if after[0] != before:
                        building_science.append(dict(building_type=key, science_A=before, science_B=after[0], science_A_restored=after[1]))
                facts.append(dict(city_id=city_id, name=row['name'], science_A=row['science'], science_B=rows[1].get(city_id, {}).get('science'),
                                  science_A_restored=rows[2].get(city_id, {}).get('science'), A_minus_B=row['science']-rows[1][city_id]['science'], changed_building_science=building_science))
            def slots(candidates):
                valid = [c for c in candidates if c['tool'] == 'get_policies']
                if not valid: return None
                return {int(i): p for i, p in re.findall(r'^  Slot (\d+) \([^\)]+\): .*?\((POLICY_[A-Z0-9_]+)\)$', valid[-1]['text'], re.M)}
            plans = [slots([c for c in window if c['request'] < policy['request']]),
                     slots([c for c in window if policy['request'] < c['request'] < (reload['request'] if reload else math.inf)]),
                     slots([c for c in window if reload and c['request'] > reload['request']])]
            checks.append(check('only_slot6_changed_and_plan_restored', bool(plans[0]) and plans[0] == plans[2]
                                and plans[1] == (dict(plans[0]) | {6: 'POLICY_PUBLIC_WORKS'}), plans))
        result.append(dict(name='T201_Rationalism_A_PublicWorks_B_reload_A', status=status(checks), checks=checks,
                           observations=[brief_ref(c) for c in (a, b, restored) if c], city_science=facts, query_statistics=query_statistics(window),
                           actions=[brief_ref(c) for c in window if c['tool'] in ('set_policies', 'reload_game_save')], excluded_from_four_stage_baseline=True,
                           boundary='Same-turn contrast only. T200-to-T201 change is not a policy effect; financial exactness is outside this parser.'))
    return result


def analyze(live):
    calls, groups, events, ingestion = load_evidence(live)
    stages = []
    for turn in STAGES:
        matches = [g for g in groups if (g.get('source') or {}).get('registration', {}).get('turn') == turn]
        if not matches:
            stages.append(dict(turn=turn, status='UNKNOWN', reason='No trusted source load in selected evidence directory.'))
            continue
        group = matches[0]
        group_calls = [c for c in calls if c['group'] == group['index']]
        ends = [c for c in group_calls if c['tool'] == 'save_game' and re.search(rf'_t{turn:03d}_result_', c['args'].get('save_name', ''))
                and c['text'].startswith('Saved: ') and not c['is_error']]
        end = ends[0]['request'] if ends else min((c['request'] for c in group_calls if c['tool'].startswith(('set_', 'end_turn'))), default=math.inf)
        scoped = [c for c in group_calls if c['turn'] == turn and c['request'] <= end]
        checks = source_checks(group, turn)
        checks.append(check('baseline_closed_by_confirmed_result_checkpoint', bool(ends), brief_ref(ends[0]) if ends else None, unknown=not ends))
        gp, mapping = analyze_gp(scoped, group, turn), analyze_map(scoped, group, turn)
        workforce, growth, production = analyze_workforce(scoped, group, turn), analyze_growth(scoped, group, turn), analyze_production(scoped, group, turn)
        settle = analyze_settle(scoped, group, turn)
        stage = dict(turn=turn, source_name=group['source']['name'], source_group=group['index'], checks=checks,
                     baseline_end_request=end if finite(end) else None, query_statistics=query_statistics(scoped),
                     gp=gp, map_search=mapping, workforce=workforce, growth=growth, production=production, settle=settle)
        stage['status'] = status(checks + [gp, mapping, workforce, growth, production, settle])
        stages.append(stage)
    final_checks = [check('ingestion_complete', ingestion['status'] == 'PASS', ingestion, unknown=ingestion['status'] != 'PASS'),
                    check('selected_session_closed_without_pending', ingestion.get('clean_session_close') is True,
                          dict(session_closed=ingestion.get('session_closed'), pending_on_exit=ingestion.get('pending_on_exit')), unknown=not ingestion.get('clean_session_close')),
                    source_code_check(live)]
    errors = [dict(**brief_ref(c), result=c['text']) for c in calls if c['is_error']]
    end200 = next((s.get('baseline_end_request') for s in stages if s['turn'] == 200), None)
    segments = independent_segments(calls, end200) if end200 is not None else []
    workforce_rows = []
    for stage in stages:
        unique = {}
        for obs in stage.get('workforce', {}).get('observations', []):
            if obs.get('rows') is not None:
                unique[obs['city_id']] = obs
        workforce_rows.extend(dict(turn=stage['turn'], city_id=city_id, name=obs.get('name'), rows=obs['rows']) for city_id, obs in unique.items())
    return dict(schema_version=1, status=status(final_checks + stages + segments), four_stage_baseline_status=status(final_checks + stages), selected_live_directory=str(live),
                checks=final_checks, stages=stages, tool_errors=errors,
                analysis_script_sha256=digest(Path(__file__).read_bytes()), completed_tool_calls=len(calls), independent_segments=segments,
                baseline_workforce_totals=dict(city_stage_observations=len(workforce_rows), native_manage_rows=sum(row['rows'] for row in workforce_rows), observations=workforce_rows),
                extra_turns=sorted({c['turn'] for c in calls if c['turn'] is not None and c['turn'] not in STAGES}),
                unscoped_relevant_requests=[brief_ref(c) for c in calls if c['tool'] in ('get_great_people', 'get_city_workforce', 'get_city_growth', 'get_city_production') and c['turn'] is None],
                limits=['This is an evidence parser; it performs no game, MCP, Lua, network, save, or strategy actions.',
                        'Text body bytes are UTF-8 tool-result text, excluding JSONL transport envelopes; RPC duration includes CLI/server handling.',
                        'Fact equality and query-byte reduction do not prove a faster model decision, equal future play, or a win-rate improvement.',
                        'Native unknowns and unsupported fields are reported; missing stages, unknown MANAGE scans, and missing pairs cannot pass.'])


def self_test():
    source = 'Current Great People timeline: native total 1; matched 1; shown 1; scan complete yes.\n'
    block = ('  Scientist: A (Modern) — Unclaimed — your points: 2/10\n'
             '    Ability: condition X\n    Patronage quote: gold 8; {gold}can patronize yes / faith 9; {faith}can patronize no\n'
             '    (individual_id: 0; class_type: GREAT_PERSON_CLASS_SCIENTIST)')
    shared = source + 'Shared balances in this response: gold 20; faith 30.\n' + block.format(gold='', faith='')
    individual = source + block.format(gold='balance 20; ', faith='balance 30; ')
    assert parse_gp(shared)['people'] == parse_gp(individual)['people']
    assert parse_gp(individual)['people'][0]['class_type'] == 'GREAT_PERSON_CLASS_SCIENTIST'
    brief = ('Row scope S1: native scope\n(1,2) RESOURCE_TEA 茶 [L]; owner=?; visible=no; facility=?; pillaged=?; actor=B; details=withheld_fog')
    assert decode_search_brief(brief) == [dict(x=1, y=2, type='RESOURCE_TEA', name='茶', **{'class': 'RESOURCECLASS_LUXURY'}, owner_id=None, visible_now=False,
                                             improvement_type=None, pillaged=None, work_unit='UNIT_BUILDER', details_status='withheld_fog', scope='native scope')]
    workforce = ("City workforce: ready | {'turn': 50, 'seed': 7, 'player_id': 0, 'civilization': 'CIVILIZATION_SUMERIA'}\n"
                 'A [id:65536] pop 7; food surplus 8.\n  Focus: favored YIELD_PRODUCTION; ignored none; others neither; unknown none.\n'
                 'Native MANAGE rows: 1; current plot yields.\n'
                 '  (40,25) [plot:1540] citizens 1/0; locked 0; worked yes; visible now yes; owner 0; assigned_here yes; FOOD=2,SCIENCE=unknown; other yields 0.')
    row, = decode_workforce_brief(workforce)['plots']
    assert row['citizens'] == 1 and row['max_citizens'] == 0 and row['visible_now'] is True
    assert row['yields']['YIELD_FOOD'] == 2 and row['yields']['YIELD_SCIENCE'] is None and row['yields']['YIELD_GOLD'] == 0
    first = '  #1 (1,2): F:unknown P:unknown\n     Current observed subtotal F:2 (1/3 tiles).\n     [B] RICE'
    assert parse_settle_blocks(first) == parse_settle_blocks(first.replace('#1 ', '#77 '))
    # More candidates than the native CLI limit require a reference chain.
    def page(request, offset, limit, total=401):
        shown = min(limit, total-offset)
        data = dict(total=total, shown=shown, offset=offset, limit=limit, omitted=total-shown,
                    truncated=offset+shown<total, next_offset=offset+shown if offset+shown<total else None,
                    scope='global', scope_note='native', order='unranked')
        call = dict(request=request, tool='get_global_settle_advisor', args=dict(offset=offset, limit=limit), data=data,
                    text_utf8_bytes=shown*20, rpc_seconds=0)
        rows = [dict(coordinates=(i, 0), facts=f'  ({i},0): F:unknown P:unknown\n     Current observed subtotal: 0') for i in range(offset, offset+shown)]
        return call, rows
    fixture = [page(i+1, offset, 200) for i, offset in enumerate((0, 200, 400))]
    fixture += [page(i+10, offset, 100) for i, offset in enumerate((0, 100, 200, 300, 400))]
    calls = [call for call, _ in fixture]
    parsed = {call['request']: rows for call, rows in fixture}
    chain = settle_chain(calls, 200, parsed, calls)
    alternate = settle_chain(calls, 100, parsed, calls)
    assert chain['complete'] and chain['restored'] == 401 and chain['requests'] == [1, 2, 3]
    assert alternate['complete'] and chain['_rows'] == alternate['_rows']
    assert not settle_chain([c for c in calls if c['request'] != 2], 200, parsed, calls)['complete']
    changed = [dict(c, data=dict(c['data'], total=402)) if c['request'] == 3 else c for c in calls]
    assert not settle_chain(changed, 200, parsed, changed)['complete']
    wrong_next = [dict(c, data=dict(c['data'], next_offset=201)) if c['request'] == 1 else c for c in calls]
    assert not settle_chain(wrong_next, 200, parsed, wrong_next)['complete']
    repeated = dict(parsed); repeated[2] = [dict(parsed[2][0], coordinates=(0, 0)), *parsed[2][1:]]
    assert not settle_chain(calls, 200, repeated, calls)['complete']
    assert status([check('missing', False, unknown=True)]) == 'UNKNOWN'
    assert status([check('wrong', False), check('missing', False, unknown=True)]) == 'FAIL'
    print('PASS: GP/map/workforce preservation; center 1/0 and unknown/zero yields; 401-row settle reference/alternate chains; gap, unstable total, wrong pointer and duplicates rejected; missing evidence remains unknown.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', default='evidence/live-v4', help='One selected CLI evidence directory, relative to run root or absolute.')
    parser.add_argument('--output', help='Explicit JSON output path. Omit to print to stdout without writing a report.')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = analyze(resolve(args.live))
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        target = resolve(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded)
        print(json.dumps(dict(status=result['status'], output=str(target), stages={str(s['turn']): s['status'] for s in result['stages']}), ensure_ascii=False))
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
