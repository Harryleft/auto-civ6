"""Read this run's saved CLI evidence; never connects to or chooses actions in Civ6.

Example: python changes/analyze_calls.py --segment baseline=evidence/baseline
  --segment play=evidence/play-t006-t100 --phases changes/analysis-phases.json
  --output evidence/call-analysis.json

Paths are relative to this run directory. A phases file maps a segment name to
nonoverlapping inclusive command-sequence ranges, e.g.
{"baseline":[{"first":30,"last":167,"phase":"play"}]}. Commands outside declared
ranges remain unclassified. Optional --stop NAME:SEQ snapshots an active log at
an explicit event boundary. No log is followed, no phase is inferred as normal
gameplay, and wall clocks are never stitched across CLI sessions.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LOADS = {'load_game_save', 'reload_game_save', 'load_save_from_menu', 'restart_and_load', 'load_save'}
UNKNOWN_CODES = {'OUTCOME_UNKNOWN', 'QUERY_UNCONFIRMED', 'TRANSPORT_OUTCOME_UNKNOWN'}
MOVEMENT_REFUSALS = {'NO_PATH', 'NO_MOVES', 'MOVE_DEFERRED'}
DECISION_CHECKS = ('stable_world_turn', 'overview_scope', 'unit_scope', 'city_scope', 'victory_scope')
ANCHOR_TOOLS = LOADS | {'get_decision_context', 'get_game_overview', 'end_turn'}


def path(value):
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def digest(data):
    return hashlib.sha256(data).hexdigest()


def seconds(start, end):
    try:
        value = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def texts(event):
    return '\n'.join(c.get('text', '') for c in event.get('result', {}).get('content', []) if c.get('type') == 'text')


def decode(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def turn_anchor(event):
    """Only accepted native receipts or a complete decision contract anchor time.

    Joining content blocks is useful for byte accounting, but could stitch partial
    or conflicting facts into a plausible anchor. These tools must return one
    successful text block; domain tools never establish the current world/turn.
    """
    if event.get('tool') not in ANCHOR_TOOLS:
        return None, None
    result = event.get('result') or {}
    if (event.get('is_error') is not False or event.get('error') is not None
            or event.get('protocol_is_error') not in (None, False)
            or result.get('isError') not in (None, False)):
        return None, 'unsuccessful_anchor_reply'
    content = result.get('content')
    if (not isinstance(content, list) or len(content) != 1
            or not isinstance(content[0], dict) or content[0].get('type') != 'text'
            or not isinstance(content[0].get('text'), str)):
        return None, 'non_single_text_anchor_reply'
    text = content[0]['text']; tool = event['tool']
    if tool == 'get_decision_context':
        body = decode(text)
        if not isinstance(body, dict): return None, 'invalid_decision_anchor'
        checks, identity = body.get('checks'), body.get('identity')
        valid = (type(body.get('schema_version')) is int and body['schema_version'] == 1
                 and body.get('status') == 'ready' and body.get('error') is None
                 and isinstance(checks, dict) and all(checks.get(k) is True for k in DECISION_CHECKS)
                 and isinstance(identity, dict)
                 and isinstance(identity.get('civilization'), str) and bool(identity['civilization'].strip())
                 and type(identity.get('seed')) is int
                 and type(identity.get('player_id')) is int and identity['player_id'] >= 0
                 and type(identity.get('turn')) is int and identity['turn'] >= 0)
        return (dict(kind='decision', turn=identity['turn'], identity={k:identity[k] for k in ('civilization','seed','player_id')}), None) if valid else (None, 'invalid_decision_anchor')
    if tool == 'get_game_overview':
        match = re.match(r'Turn (\d+) \|', text)
        return (dict(kind='overview', turn=int(match[1])), None) if match else (None, 'invalid_overview_anchor')
    if tool in LOADS:
        match = re.match(r'Loaded [^\r\n]+: ([A-Za-z_]+), seed (-?\d+), turn (\d+)\.(?:\n|$)', text)
        return (dict(kind='load', turn=int(match[3]), identity=dict(civilization=match[1],seed=int(match[2]))), None) if match else (None, 'unconfirmed_load_boundary')
    match = re.match(r'Turn (\d+) -> (\d+)(?:\n|$)', text)
    if match and int(match[2]) > int(match[1]):
        return dict(kind='advance', turn=int(match[1]), to_turn=int(match[2])), None
    return None, 'invalid_advance_receipt'


def unknown_statuses(value, key='$'):
    """Explicit statuses only: null outcome/quotes and static unverified flags are not errors."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == 'status' and v in ('unknown', 'partial', 'outcome_unknown'):
                out.append(f'{key}.status={v}')
            elif isinstance(v, (dict, list)):
                out += unknown_statuses(v, f'{key}.{k}')
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += unknown_statuses(v, f'{key}[{i}]')
    return out


def coded_receipts(event):
    """Extract explicit top-level result codes, never keywords inside prose/JSON.

    Codes may originate in client preflight, Runtime guards or native facts;
    their presence does not establish native eligibility rejection or submission.

    NOT_READY preserves an existing activity and is not an error. In particular,
    a confirmed MOVING_TO ... BLOCKED ZOC segment is not a no-submit refusal.
    Submission counts still require the native action evidence, not these codes.
    """
    receipts = []
    for block in (event.get('result') or {}).get('content', []):
        if block.get('type') != 'text' or not isinstance(block.get('text'), str):
            continue
        first = block['text'].splitlines()[0] if block['text'] else ''
        if event.get('tool') == 'end_turn' and first.startswith(('Cannot end turn: NOT_SUBMITTED (', 'Cannot end turn — resolve these blockers:')):
            receipts.append(dict(code='ENDTURN_NOT_SUBMITTED' if first.startswith('Cannot end turn:') else 'ENDTURN_BLOCKERS',
                                 kind='end_turn_blocker', text=block['text'], result_sequence=event['sequence']))
            continue
        match = re.match(r'^(?:(?:ERR:|Error: )?)([A-Z][A-Z0-9_]*)\|', first)
        if not match:
            continue
        code = match[1]
        if first.startswith(('ERR:', 'Error: ')) or code in ('NOT_READY', 'ALREADY_WAITING'):
            receipts.append(dict(code=code, kind='movement_refusal' if code in MOVEMENT_REFUSALS
                                 else 'activity_preserved' if code in ('NOT_READY', 'ALREADY_WAITING')
                                 else 'reported_error', text=first, result_sequence=event['sequence']))
    return receipts


def summarize(rows):
    return dict(commands=len(rows), categories={k: sum(r['category'] == k for r in rows) for k in sorted({r['category'] for r in rows})},
                text_utf8_bytes=sum(r['text_utf8_bytes'] for r in rows),
                rpc_seconds=sum(r['rpc_seconds'] for r in rows) if all(r['rpc_seconds'] is not None for r in rows) else None,
                cli_seconds=sum(r['cli_seconds'] for r in rows) if all(r['cli_seconds'] is not None for r in rows) else None,
                error_commands=sum(bool(r['errors']) for r in rows), unknown_commands=sum(bool(r['unknown']) for r in rows),
                blocked_query_commands=sum(bool(r['structured_blocks']) for r in rows),
                result_codes={code:sum(p['code'] == code for r in rows for p in r['coded_receipts'])
                              for code in sorted({p['code'] for r in rows for p in r['coded_receipts']})},
                explicit_advances=[r['advance'] for r in rows if r['advance']],
                explicit_confirmation_mentions=sum(bool(r['confirmation_mentions']) for r in rows))


def phase_for(seq, ranges):
    matches = [r['phase'] for r in ranges if r['first'] <= seq <= r['last']]
    if len(matches) > 1:
        raise ValueError(f'Overlapping phase ranges at command {seq}')
    return matches[0] if matches else 'unclassified'


def read_segment(name, directory, ranges, stop=None):
    raw = (directory / 'debug.jsonl').read_bytes()  # one fixed read, never tail/follow
    events, issues = [], []
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
            if stop is None or event['sequence'] <= stop:
                events.append(event)
        except (ValueError, KeyError):
            issues.append(dict(line=line_number, reason='invalid_jsonl_line'))
    selected = json.dumps(events,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    snapshots = {'debug.jsonl': dict(sha256=digest(raw), bytes=len(raw), stop_sequence=stop,
                                   selected_event_count=len(events),selected_events_sha256=digest(selected))}
    if stop is None and not any(e['event'] == 'session_closing' for e in events):
        raise ValueError(f'{name}: active evidence needs an explicit --stop event boundary')
    files = {}
    for filename in ('command-timing.json', 'turn-timing.json', 'debug-tools.json', 'context.json'):
        p = directory / filename
        if p.exists():
            data = p.read_bytes(); snapshots[filename] = dict(sha256=digest(data), bytes=len(data)); files[filename] = json.loads(data)
    hints = {t['name']: (t.get('annotations') or {}).get('readOnlyHint') for t in files.get('debug-tools.json', {}).get('tools', [])}
    timing = {str(r['request_id']).rsplit(':', 1)[-1]: r for r in files.get('command-timing.json', {}).get('commands', [])}
    commands, current, turn, world_segment, world_identity = [], None, None, 0, None
    for e in events:
        if e['event'] == 'command':
            current = dict(sequence=e['sequence'], command=e['input'], timestamp=e['timestamp'], turn=turn, world_segment=world_segment, events=[])
            commands.append(current)
        elif current is not None:
            current['events'].append(e)
    rows = []; window = 0; previous_phase = None; unconfirmed_boundaries = 0
    for c in commands:
        es = c.pop('events'); starts = {e['sequence']: e for e in es if e['event'] == 'tool_started'}
        replies = [e for e in es if e['event'] == 'tool_result']; observed = turn; advance = None
        anchor_replies = [e for e in replies if e.get('tool') in ANCHOR_TOOLS]
        anchor = None; anchor_event = None
        if len(anchor_replies) > 1:
            issues.append(dict(sequence=c['sequence'], reason='multiple_anchor_replies', results=[e['sequence'] for e in anchor_replies]))
        elif anchor_replies:
            anchor_event = anchor_replies[0]; anchor, reason = turn_anchor(anchor_event)
            if reason: issues.append(dict(sequence=anchor_event['sequence'], reason=reason))
        unconfirmed_boundary = any(e.get('tool') in LOADS and e['event'] in ('tool_started', 'tool_result', 'tool_outcome_unknown') for e in es) and not (anchor and anchor['kind'] == 'load')
        if unconfirmed_boundary:
            # A failed/unknown load is not a confirmed new world. Separate timing
            # and discard the old turn until a later accepted observation.
            unconfirmed_boundaries += 1; observed = turn = None
            issues.append(dict(sequence=c['sequence'], reason='unconfirmed_load_boundary'))
        if anchor:
            observed = anchor['turn']
            identity = anchor.get('identity')
            changed = bool(identity and world_identity and any(identity[k] != world_identity[k]
                           for k in identity.keys() & world_identity.keys()))
            if anchor['kind'] == 'load' or changed:
                world_segment += 1
            if changed:
                issues.append(dict(sequence=c['sequence'],reason='observed_world_identity_changed',
                                   before=world_identity,after=identity))
            if identity:
                world_identity = identity
            if anchor['kind'] == 'advance':
                advance = dict(from_turn=anchor['turn'], to_turn=anchor['to_turn'], request=anchor_event['request'], confirmed_at=anchor_event['timestamp'])
        errors = [dict(event=e['event'], sequence=e['sequence'], error=e.get('error'), message=e.get('message')) for e in es if e.get('is_error') or e['event'] == 'command_blocked']
        unknown = [str((e.get('error') or {}).get('code')) for e in es if (e.get('error') or {}).get('code') in UNKNOWN_CODES or e['event'] == 'tool_outcome_unknown']
        structured_blocks = []
        for e in replies:
            for block in (e.get('result') or {}).get('content', []):
                if block.get('type') == 'text':
                    decoded = decode(block.get('text'))
                    unknown += unknown_statuses(decoded)
                    if isinstance(decoded, dict) and decoded.get('status') == 'blocked':
                        structured_blocks.append(dict(result_sequence=e['sequence'],status='blocked',pending=decoded.get('pending')))
            if texts(e).startswith(('OUTCOME_UNKNOWN', 'QUERY_UNCONFIRMED')):
                unknown.append('explicit_unknown_text_prefix')
        rpc_parts = [seconds(starts[e['request']]['timestamp'], e['timestamp']) if e.get('request') in starts else None for e in es if e['event'] in ('tool_result', 'tool_outcome_unknown')]
        missing = sorted(set(starts) - {e.get('request') for e in es if e['event'] in ('tool_result', 'tool_outcome_unknown')})
        if missing:
            rpc_parts.append(None); unknown.append('incomplete_tool_reply')
            issues.append(dict(sequence=c['sequence'], reason='incomplete_tool_reply', requests=missing))
        ts = timing.get(str(c['sequence']), {}); tool_names = [e['tool'] for e in replies]
        if any(e['event'] == 'dry_run' for e in es): category = 'schema_dryrun'
        elif any(e['event'] == 'help' for e in es): category = 'help'
        elif replies: category = 'read' if all(hints.get(t) is True for t in tool_names) else 'mutation_or_lifecycle_call'
        else: category = 'local_or_incomplete'
        mentions = [texts(e) for e in replies if 'Action confirmation:' in texts(e) or texts(e).startswith(('CONFIRMED|', 'POLICIES_SET|', 'FOCUS_SET|'))]
        phase = phase_for(c['sequence'], ranges)
        range_id = next((i for i,r in enumerate(ranges) if r['first'] <= c['sequence'] <= r['last']), None)
        if (phase, range_id, unconfirmed_boundaries, world_segment) != previous_phase:
            window += 1; previous_phase = (phase, range_id, unconfirmed_boundaries, world_segment)
        row = dict(**{k: v for k, v in c.items() if k not in ('turn', 'world_segment')}, segment=name,
                   phase=phase, phase_range=range_id, window=window, world_segment=world_segment,
                   last_confirmed_world_identity=world_identity, turn=observed,
                   turn_anchor=anchor['kind'] if anchor else None, unconfirmed_boundary=unconfirmed_boundary, category=category,
                   tool_names=tool_names, requests=[e['sequence'] for e in es if e['event'] == 'tool_started'],
                   text_utf8_bytes=sum(len(b.get('text','').encode()) for e in replies
                                       for b in (e.get('result') or {}).get('content',[]) if b.get('type') == 'text'),
                   rpc_seconds=sum(rpc_parts) if all(v is not None for v in rpc_parts) else None,
                   cli_seconds=ts.get('cli_seconds'), finished=ts.get('finished'), started=ts.get('started'),
                   timing_clock=ts.get('clock_source'), errors=errors, unknown=unknown, advance=advance,
                   structured_blocks=structured_blocks,
                   coded_receipts=[p for e in replies for p in coded_receipts(e)],
                   confirmation_mentions=mentions, result_sequences=[e['sequence'] for e in replies])
        rows.append(row); turn = advance['to_turn'] if advance else observed
    rounds = []
    for key in sorted({(r['world_segment'], r['turn'], r['phase'], r['window']) for r in rows}, key=str):
        selected = [r for r in rows if (r['world_segment'], r['turn'], r['phase'], r['window']) == key]
        a, b = selected[0].get('started'), selected[-1].get('finished')
        span = b-a if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a else None
        summary = summarize(selected); cli = summary['cli_seconds']
        prior = [r['advance'] for r in rows if r['world_segment'] == key[0] and r['window'] == key[3] and r['advance'] and r['advance']['to_turn'] == key[1] and r['sequence'] < selected[0]['sequence']]
        completed = summary['explicit_advances']
        wall = seconds(prior[-1]['confirmed_at'], completed[-1]['confirmed_at']) if prior and completed else span
        gap = wall-cli if wall is not None and cli is not None and wall >= cli-.001 else None
        rounds.append(dict(segment=name, world_segment=key[0], turn=key[1], phase=key[2], window=key[3], **summary,
                           observed_command_span_seconds=span, wall_seconds=wall,
                           wall_start_basis='previous_confirmed_end' if prior and completed else 'first_observed_command',
                           gap_seconds=max(0, gap) if gap is not None else None,
                           status='confirmed_advance' if summary['explicit_advances'] else 'partial_observation'))
    native = [r['advance'] for r in rows if r['advance']]
    exported = files.get('turn-timing.json', {})
    if stop is None and 'turn-timing.json' in files and exported.get('completed_turns') != len(native):
        issues.append(dict(reason='exported_completed_turn_count_disagrees', exported=exported.get('completed_turns'), explicit_receipts=len(native)))
    return dict(name=name, directory=str(directory), snapshots=snapshots, closed=any(e['event'] == 'session_closing' for e in events),
                source_sha256=files.get('context.json', {}).get('source_sha256'), issues=issues, rows=rows, rounds=rounds,
                totals=summarize(rows), phases={p: summarize([r for r in rows if r['phase'] == p]) for p in sorted({r['phase'] for r in rows})}), events


def initial_opportunities(events):
    """Fixed T1–T4 examples from this run; projections are not game acceptance."""
    if not events or events[0].get('run_id') != 'eac07ca55c044903b88d2b628c14b994':
        return dict(status='NOT_RUN', reason='Not the pinned initial CLI run')
    results = {e['sequence']: e for e in events if e['event'] == 'tool_result' and e['sequence'] <= 149}
    projections = []
    for seq in (24, 27):
        if seq not in results: continue
        old = texts(results[seq]); body = json.loads(old); profile = body['profile']
        canonical = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        ref = {k: v for k, v in profile.items() if not isinstance(v, (dict, list))}
        ref.update(sha256=digest(canonical), details_level='evidence', current_session_verified=False, active_mods_verified=False, active_modes_verified=False)
        candidate = body | {'profile': ref}; encoded = json.dumps(candidate, ensure_ascii=False).encode()
        projections.append(dict(result_sequence=seq, full_utf8_bytes=len(old.encode()), projected_utf8_bytes=len(encoded), saved=len(old.encode())-len(encoded), all_non_profile_fields_equal=all(candidate[k] == body[k] for k in body if k != 'profile'), kind='offline_projection_not_modified_CLI'))
    needed = (32, 38, 69, 72, 66, 105, 108, 124, 127, 143)
    if not all(n in results for n in needed): return dict(status='UNKNOWN', profile_projections=projections, reason='Pinned examples incomplete')
    a, b = json.loads(texts(results[69])), json.loads(texts(results[72]))
    return dict(status='observed_examples', event_boundary=149, profile_projections=projections,
                repeated_victory=dict(results=[32, 38], text_utf8_bytes=len(texts(results[38]).encode()), exact_equal=json.loads(texts(results[32]))['sections']['victory']['data'] == texts(results[38])),
                repeated_default_decision=dict(results=[69, 72], exact_default_sections_equal=all(v == b['sections'][k] for k, v in a['sections'].items()), repeated_sections=list(a['sections']), extra_research_text_utf8_bytes=len(b['sections']['research']['data'].encode()), existing_alternative='research show / technology status / civic status'),
                movement_examples=[dict(result_sequence=n, text=texts(results[n]), text_utf8_bytes=len(texts(results[n]).encode())) for n in (66, 105, 108, 124, 127, 143)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--segment', action='append', default=[], metavar='NAME=DIR')
    parser.add_argument('--stop', action='append', default=[], metavar='NAME:EVENT_SEQUENCE')
    parser.add_argument('--phases'); parser.add_argument('--output')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        assert unknown_statuses({'status':'ready','outcome':None,'quote':None,'active_mods_verified':False}) == []
        assert unknown_statuses({'sections':{'a':{'status':'unknown'}}}) == ['$.sections.a.status=unknown']
        assert phase_for(5,[dict(first=1,last=4,phase='setup')]) == 'unclassified'
        assert seconds('2026-10-31T00:00:01+00:00','2026-10-31T00:00:00+00:00') is None
        def native(value):
            return coded_receipts(dict(sequence=1,result=dict(content=[dict(type='text',text=value)])))
        assert native('ERR:NO_PATH|Visible destination has no native movement path; no move submitted.')[0]['kind'] == 'movement_refusal'
        assert native('Error: NO_PATH|Visible destination has no native movement path; no move submitted.')[0]['code'] == 'NO_PATH'
        assert native('NOT_READY|activity:3|Existing activity preserved')[0]['kind'] == 'activity_preserved'
        assert native('MOVING_TO|1,2|BLOCKED (native segment complete in zone of control; no displacement)') == []
        assert native('{"reason":"ERR:NO_MOVES|not a native receipt"}') == []
        assert native('A rule mentions NO_PATH and NOT_READY in an example.') == []
        assert coded_receipts(dict(sequence=1,tool='end_turn',result=dict(content=[dict(type='text',text='Cannot end turn — resolve these blockers:\n  - Research: 选择1项科技')])))[0]['kind'] == 'end_turn_blocker'
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def event(kind, sequence, **data):
                return dict(event=kind,sequence=sequence,run_id='test',timestamp=f'2026-10-31T00:00:{sequence:02d}+00:00',**data)
            def reply(sequence, request, tool, value):
                return event('tool_result',sequence,request=request,tool=tool,is_error=False,result=dict(content=[dict(type='text',text=value)]))
            valid_decision = dict(schema_version=1, status='ready', checks=dict.fromkeys(DECISION_CHECKS, True),
                                  identity=dict(civilization='sumeria',seed=-1,player_id=0,turn=1),outcome=None)
            def decision_reply(value):
                return reply(4,3,'get_decision_context',json.dumps(value))
            assert turn_anchor(decision_reply(valid_decision))[0]['turn'] == 1
            invalid_decisions = [valid_decision | {'checks':{}}, valid_decision | {'status':'blocked'},
                                 valid_decision | {'error':{'code':'FAILED'}}, valid_decision | {'schema_version':True}]
            invalid_decisions += [valid_decision | {'checks':{k:v for k,v in valid_decision['checks'].items() if k != missing}} for missing in DECISION_CHECKS]
            invalid_decisions += [valid_decision | {'checks':valid_decision['checks'] | {'stable_world_turn':1}}]
            invalid_decisions += [valid_decision | {'identity':valid_decision['identity'] | {k:v}}
                                  for k,v in [('civilization',' '),('seed',True),('player_id',True),('player_id',-1),('turn',True),('turn',-1)]]
            for body in invalid_decisions: assert turn_anchor(decision_reply(body))[0] is None
            for field,value in [('is_error',True),('error',{'code':'FAILED'}),('protocol_is_error',True)]:
                assert turn_anchor(decision_reply(valid_decision) | {field:value})[0] is None
            for tool,value in [('get_units',json.dumps(valid_decision)),('get_city', 'Turn 999 | unrelated')]:
                assert turn_anchor(reply(4,3,tool,value)) == (None,None)
            multiple = decision_reply(valid_decision)
            multiple['result']['content'].append(dict(type='text',text=json.dumps(valid_decision | {'status':'unknown'})))
            assert turn_anchor(multiple)[0] is None
            fake = [event('command',2,input='state decision'),event('tool_started',3,tool='get_decision_context'),
                    decision_reply(valid_decision),
                    event('command',5,input='turn end'),event('tool_started',6,tool='end_turn'),reply(7,6,'end_turn','Turn 1 -> 2'),
                    event('command',8,input='state overview'),event('tool_started',9,tool='get_game_overview'),reply(10,9,'get_game_overview','Turn 3 | observed only'),
                    event('command',11,input='state overview'),event('tool_started',12,tool='get_game_overview'),event('session_closing',13)]
            (directory/'debug.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in fake))
            report,_=read_segment('test',directory,[dict(first=2,last=7,phase='play'),dict(first=8,last=13,phase='play')])
            assert len(report['totals']['explicit_advances']) == 1  # higher overview is not another confirmed turn
            assert report['rows'][0]['unknown'] == [] and report['rows'][-1]['rpc_seconds'] is None
            assert report['rows'][-1]['unknown'] == ['incomplete_tool_reply']
            assert report['rows'][0]['window'] != report['rows'][2]['window']  # same phase in distinct ranges
            # Replay invalid observations after a valid anchor: neither unrelated
            # JSON nor unrelated Turn text may change the last known turn.
            cases = [('get_decision_context',json.dumps(valid_decision),False),
                     ('get_decision_context',json.dumps(valid_decision | {'status':'blocked','identity':valid_decision['identity'] | {'turn':999}}),False),
                     ('get_units',json.dumps(valid_decision | {'identity':valid_decision['identity'] | {'turn':999}}),False),
                     ('get_city','Turn 999 | unrelated',False),
                     ('load_game_save','Loaded bad: sumeria, seed -1, turn 99.',True),
                     ('load_game_save','Loaded good: sumeria, seed -1, turn 2.',False),
                     ('end_turn','Turn 2 -> 3',False)]
            fake = []
            for i,(tool,value,error) in enumerate(cases):
                seq = 3*i+1
                fake += [event('command',seq,input=tool),event('tool_started',seq+1,tool=tool),
                         reply(seq+2,seq+1,tool,value) | {'is_error':error}]
            fake.append(event('session_closing',22))
            (directory/'debug.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in fake))
            report,_ = read_segment('test',directory,[])
            assert [r['turn'] for r in report['rows']] == [1,1,1,1,None,2,2]
            assert report['totals']['blocked_query_commands'] == 1
            assert [r['world_segment'] for r in report['rows']] == [0,0,0,0,0,1,1]
            assert report['rows'][4]['unconfirmed_boundary'] and report['rows'][4]['window'] != report['rows'][3]['window']
            assert len(report['totals']['explicit_advances']) == 1
            # A changed seed is a world boundary even without a load receipt.
            # Mixed content cannot hide one explicit unknown status.
            fake = []
            for seq,tool,value in [(1,'get_decision_context',json.dumps(valid_decision | {'identity':valid_decision['identity'] | {'seed':7}})),
                                   (4,'end_turn','Turn 1 -> 2'),
                                   (7,'get_decision_context',json.dumps(valid_decision | {'identity':valid_decision['identity'] | {'seed':8,'turn':2}})),
                                   (10,'end_turn','Turn 2 -> 3'),
                                   (13,'get_units','{"status":"unknown"}')]:
                fake += [event('command',seq,input=tool),event('tool_started',seq+1,tool=tool),reply(seq+2,seq+1,tool,value)]
            fake[-1]['result']['content'].append(dict(type='text',text='{"status":"ready"}'))
            fake.append(event('session_closing',16))
            (directory/'debug.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in fake))
            report,_=read_segment('test',directory,[])
            assert [r['world_segment'] for r in report['rows']] == [0,0,1,1,1]
            assert report['rows'][0]['window'] != report['rows'][2]['window']
            assert next(r for r in report['rounds'] if r['world_segment']==1 and r['turn']==2)['wall_start_basis']=='first_observed_command'
            assert report['rows'][-1]['unknown'] == ['$.status=unknown']
            assert report['rows'][-1]['text_utf8_bytes'] == len('{"status":"unknown"}{"status":"ready"}'.encode())
        print('PASS: strict decision/native anchors, rejected blocked/error/incomplete/domain/multi-content anchors, unconfirmed load, RPC and phase boundaries'); return
    phases = json.loads(path(args.phases).read_text()) if args.phases else {}
    stops = {v.rsplit(':',1)[0]:int(v.rsplit(':',1)[1]) for v in args.stop}
    reports, opportunities = [], []; names = set()
    for item in args.segment:
        name, directory = item.split('=',1)
        if name in names: parser.error('Segment names must be unique')
        names.add(name)
        report, events = read_segment(name, path(directory), phases.get(name,[]), stops.get(name))
        reports.append(report)
        if name == 'baseline': opportunities.append(initial_opportunities(events))
    if not reports: parser.error('Supply at least one explicit --segment NAME=DIR')
    output = dict(schema_version=2, analysis_script_sha256=digest(Path(__file__).read_bytes()), segments=reports, initial_opportunities=opportunities,
                  by_phase={p:summarize([r for s in reports for r in s['rows'] if r['phase']==p]) for p in sorted({r['phase'] for s in reports for r in s['rows']})},
                  limitations=['No continuous wall clock is reconstructed across CLI sessions, worlds or phase ranges.',
                               'Observed command spans omit waiting before the first command; gaps include model/provider/shell/human/handoff time, not pure thinking.',
                               'Mutation calls are not proof of submission or confirmation. Confirmation mentions are explicit text markers, not all native confirmations and not proof a query was necessary.',
                               'RPC timestamps measure CLI-to-tool response latency, not native engine CPU time.',
                               'Only declared phase ranges count as play; unclassified setup/closing/handoff are retained separately.',
                               'Input files are read once; active-log snapshots need an explicit --stop and are not an atomic set of files.'])
    encoded = json.dumps(output,ensure_ascii=False,indent=2)+'\n'
    if args.output: path(args.output).write_text(encoded)
    else: print(encoded,end='')


if __name__ == '__main__': main()
