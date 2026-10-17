"""Standalone knowledge commands tested through their public file and CLI seam."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def cli(root, *args, ok=True):
    result = subprocess.run([sys.executable, str(ROOT / 'civ6_knowledge/query.py'), '--root', str(root), *args], capture_output=True, text=True)
    assert result.returncode == (0 if ok else 2), result.stderr + result.stdout
    return json.loads(result.stdout)


def snapshot():
    return {
        'schema_version': 1,
        'profile': {'game_version': '1.0.12.54', 'macos_bundle_version': '1.4.6', 'macos_bundle_build': '376653.178', 'steam_build_id': '15296837', 'ruleset': 'RULESET_EXPANSION_2', 'source_scope': 'local_cached_rules_snapshot', 'current_session_verified': False, 'active_mods_verified': False, 'active_modes_verified': False},
        'tables': {
            'Civics': [{'CivicType': 'CIVIC_CODE_OF_LAWS', 'Name': 'LOC_LAWS', 'Cost': 20}],
            'Technologies': [{'TechnologyType': 'TECH_POTTERY', 'Name': 'LOC_POTTERY', 'Cost': 25}, {'TechnologyType': 'TECH_WRITING', 'Name': 'LOC_WRITING', 'Cost': 50}],
            'CivicPrereqs': [],
            'TechnologyPrereqs': [{'Technology': 'TECH_WRITING', 'PrereqTech': 'TECH_POTTERY'}],
            'Districts': [{'DistrictType': 'DISTRICT_CAMPUS', 'Name': 'LOC_CAMPUS', 'PrereqTech': 'TECH_WRITING', 'Cost': 54, 'RequiresPopulation': 1}],
            'Policies': [{'PolicyType': 'POLICY_DISCIPLINE', 'Name': 'LOC_DISCIPLINE', 'PrereqCivic': 'CIVIC_CODE_OF_LAWS', 'GovernmentSlotType': 'SLOT_MILITARY', 'TraitType': 'TRAIT_TEST'}],
            'Boosts': [{'TechnologyType': 'TECH_WRITING', 'Boost': 40, 'TriggerDescription': 'LOC_BOOST_WRITING'}],
            'District_Adjacencies': [{'DistrictType': 'DISTRICT_CAMPUS', 'YieldChangeId': 'Mountains_Science'}],
            'Adjacency_YieldChanges': [{'ID': 'Mountains_Science', 'YieldType': 'YIELD_SCIENCE', 'YieldChange': 1, 'AdjacentTerrain': 'TERRAIN_MOUNTAIN'}],
            'Resolutions': [{'ResolutionType': 'RESOLUTION_TEST', 'Name': 'LOC_RESOLUTION', 'Description': 'LOC_RESOLUTION_DESC'}],
            'ProposalTypes': [{'ProposalType': 'PROPOSAL_TEST', 'Name': 'LOC_PROPOSAL'}],
            'VotingBlockers': [{'VotingBlockerType': 'BLOCKER_TEST', 'ResolutionType': 'RESOLUTION_TEST', 'BlocksUp': 1, 'BlocksDown': 0}],
        },
        'localization': {'LOC_WRITING': {'zh_Hans_CN': '写作', 'en_US': 'Writing'}, 'LOC_POTTERY': {'en_US': 'Pottery'}, 'LOC_CAMPUS': {'zh_Hans_CN': '学院', 'en_US': 'Campus'}, 'LOC_LAWS': {'zh_Hans_CN': '法典', 'en_US': 'Code of Laws'}, 'LOC_BOOST_WRITING': {'zh_Hans_CN': '遇见另一个文明'}, 'LOC_DISCIPLINE': {'zh_Hans_CN': '纪律'}, 'LOC_RESOLUTION_DESC': {'zh_Hans_CN': '测试决议说明'}},
        'code_evidence': [], 'sources': [],
    }


def legacy_distribution(tmp_path):
    # Keep four-topic fixtures independent of later topic definitions.
    distribution = tmp_path / 'legacy-distribution'
    if not distribution.exists():
        shutil.copytree(ROOT / 'civ6_knowledge', distribution, ignore=shutil.ignore_patterns('*.json', '__pycache__'))
    return distribution


def build(tmp_path, data=None):
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(data or snapshot()), encoding='utf-8')
    output = tmp_path / 'knowledge'
    result = subprocess.run([sys.executable, str(legacy_distribution(tmp_path) / 'build.py'), '--source', str(source), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_build_lookup_is_portable_and_summary_does_not_expand_evidence(tmp_path):
    root = build(tmp_path)
    moved = tmp_path / 'copied'
    shutil.copytree(root, moved)
    assert cli(moved, 'search', '写作')['results'][0]['id'] == 'TECH_WRITING'
    assert cli(moved, 'search', 'writing')['results'][0]['id'] == 'TECH_WRITING'
    card = cli(moved, 'show', 'TECH_WRITING')
    assert card['names']['en_US'] == 'Writing'
    assert 'rule' not in card and 'evidence' not in card and 'source_refs' not in card
    assert cli(moved, 'validate')['status'] == 'valid'


def test_rule_planning_retains_prerequisites_unlock_conditions_and_adjacency(tmp_path):
    root = build(tmp_path)
    writing = cli(root, 'show', 'TECH_WRITING', '--level', 'rule')
    assert writing['rule']['prerequisites'][0]['id'] == 'TECH_POTTERY'
    assert writing['rule']['unlocks'][0]['id'] == 'DISTRICT_CAMPUS'
    assert writing['rule']['boosts'][0]['trigger']['zh_Hans_CN'] == '遇见另一个文明'
    law = cli(root, 'show', 'CIVIC_CODE_OF_LAWS', '--level', 'rule')
    assert law['rule']['unlocks'][0]['conditions']['TraitType'] == 'TRAIT_TEST'
    assert law['relations'][0]['coverage'] == 'external'
    campus = cli(root, 'show', 'DISTRICT_CAMPUS', '--level', 'rule')
    assert campus['rule']['facts']['RequiresPopulation'] == 1
    assert campus['rule']['adjacencies'][0]['facts']['AdjacentTerrain'] == 'TERRAIN_MOUNTAIN'
    evidence = cli(root, 'show', 'DISTRICT_CAMPUS', '--level', 'evidence')
    assert any(entry['facts'].get('ID') == 'Mountains_Science' for entry in evidence['evidence'])
    assert not any(entry['source_ref']['table'] == 'Technologies' for entry in evidence['evidence'])
    assert cli(root, 'show', 'TECH_POTTERY')['names']['zh_Hans_CN'] == 'Pottery'


def test_search_truncation_unknown_entities_and_version_mismatch_are_explicit(tmp_path):
    root = build(tmp_path)
    found = cli(root, 'search', 'TECH_', '--limit', '1')
    assert (found['total'], found['truncated'], found['results'][0]['id']) == (2, True, 'TECH_POTTERY')
    assert cli(root, 'search', 'not_a_thing')['status'] == 'not_found'
    assert 'unknown ID' in cli(root, 'show', 'NO_SUCH_RULE', ok=False)['error']
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps(snapshot()['profile']), encoding='utf-8')
    assert cli(root, 'check-profile', str(profile))['current_session_verified'] is False
    mismatched = snapshot()['profile'] | {'game_version': 'different'}
    profile.write_text(json.dumps(mismatched), encoding='utf-8')
    assert 'profile incompatible' in cli(root, '--profile', str(profile), 'show', 'TECH_WRITING', ok=False)['error']


def test_congress_preserves_asymmetric_and_all_blocked_definitions(tmp_path):
    data = snapshot()
    data['tables']['VotingBlockers'] = [
        {'VotingBlockerType': 'BLOCKER_ONE_WAY', 'NoUpvote': 1, 'NoDownvote': 0},
        {'VotingBlockerType': 'BLOCKER_BOTH', 'NoUpvote': 1, 'NoDownvote': 1},
    ]
    root = build(tmp_path, data)
    assert cli(root, 'show', 'BLOCKER_ONE_WAY', '--level', 'rule')['rule']['facts']['NoDownvote'] == 0
    blocked = cli(root, 'show', 'BLOCKER_BOTH', '--level', 'rule')
    assert (blocked['rule']['facts']['NoUpvote'], blocked['rule']['facts']['NoDownvote']) == (1, 1)
    assert any('UNKNOWN' in note for note in blocked['rule']['limitations'])


def test_deterministic_rebuild_and_corruption_detection(tmp_path):
    root = build(tmp_path)
    first = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*.json')}
    build(tmp_path)
    assert first == {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*.json')}
    path = root / 'rules/technologies.json'
    doc = json.loads(path.read_text())
    doc['cards'][0]['source_refs'][0]['row'] = 99
    path.write_text(json.dumps(doc))
    assert 'fingerprint' in cli(root, 'validate', ok=False)['error']
    # Even if a caller updates the file checksum, invalid semantic references are rejected.
    import hashlib
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['files']['rules/technologies.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest))
    assert 'source references' in cli(root, 'validate', ok=False)['error']


def test_incomplete_sources_and_dangling_prerequisites_fail_without_output(tmp_path):
    data = snapshot()
    del data['tables']['VotingBlockers']
    source = tmp_path / 'bad.json'
    source.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, str(legacy_distribution(tmp_path) / 'build.py'), '--source', str(source), '--output', str(tmp_path / 'bad')], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'missing primary table' in json.loads(result.stdout)['error']
    assert not (tmp_path / 'bad').exists()
    data = snapshot()
    data['tables']['TechnologyPrereqs'][0]['PrereqTech'] = 'TECH_ABSENT'
    source.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, str(legacy_distribution(tmp_path) / 'build.py'), '--source', str(source), '--output', str(tmp_path / 'bad')], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'dangling prerequisite' in json.loads(result.stdout)['error']


def test_code_evidence_has_a_verifiable_content_fingerprint(tmp_path):
    data = snapshot()
    data['code_evidence'] = [{'id': 'voting_gate', 'path': 'Base/WorldCongress.lua', 'line_start': 10, 'line_end': 11, 'sha256': 'a' * 64, 'text': 'eligible = check()', 'text_sha256': 'b' * 64}]
    root = build(tmp_path, data)
    assert 'code evidence fingerprint' in cli(root, 'validate', ok=False)['error']


def test_research_relations_support_planning_in_both_directions(tmp_path):
    root = build(tmp_path)
    pottery = cli(root, 'show', 'TECH_POTTERY', '--level', 'rule')
    assert any(link['type'] == 'enables_research' and link['target'] == 'TECH_WRITING' and link['coverage'] == 'internal' for link in pottery['relations'])


def test_evidence_includes_only_the_referenced_localized_source_text(tmp_path):
    root = build(tmp_path)
    evidence = cli(root, 'show', 'TECH_WRITING', '--level', 'evidence')
    assert evidence['localization']['LOC_WRITING']['en_US'] == 'Writing'
    assert evidence['localization']['LOC_BOOST_WRITING']['zh_Hans_CN'] == '遇见另一个文明'
    assert 'LOC_LAWS' not in evidence['localization']


def test_resolution_effect_and_district_trait_ownership_remain_traceable(tmp_path):
    data = snapshot()
    data['tables']['Districts'][0]['TraitType'] = 'TRAIT_CAMPUS'
    data['tables']['CivilizationTraits'] = [{'CivilizationType': 'CIVILIZATION_TEST', 'TraitType': 'TRAIT_CAMPUS'}]
    data['tables']['LeaderTraits'] = [{'LeaderType': 'LEADER_TEST', 'TraitType': 'TRAIT_CAMPUS'}]
    data['tables']['ResolutionEffects'] = [{'ResolutionEffectId': 1, 'ResolutionType': 'RESOLUTION_TEST', 'WhichEffect': 1, 'ModifierId': 'MODIFIER_TEST'}]
    root = build(tmp_path, data)
    district = cli(root, 'show', 'DISTRICT_CAMPUS', '--level', 'evidence')
    assert any(row['facts'].get('CivilizationType') == 'CIVILIZATION_TEST' for row in district['evidence'])
    assert any(row['facts'].get('LeaderType') == 'LEADER_TEST' for row in district['evidence'])
    resolution = cli(root, 'show', 'RESOLUTION_TEST', '--level', 'evidence')
    assert any(row['facts'].get('ModifierId') == 'MODIFIER_TEST' for row in resolution['evidence'])


def test_evidence_filters_native_excerpts_to_the_selected_topic(tmp_path):
    import hashlib
    data = snapshot()
    ids = ['technology-live-prerequisites', 'civic-live-prerequisites', 'district-adjacency-calculation', 'congress-voting-blockers', 'congress-stage-flow']
    data['code_evidence'] = [{'id': ident, 'path': 'game/' + ident + '.lua', 'line_start': 1, 'line_end': 1, 'sha256': 'a' * 64, 'text': ident, 'text_sha256': hashlib.sha256(ident.encode()).hexdigest()} for ident in ids]
    root = build(tmp_path, data)
    for ident, expected in [('TECH_WRITING', ids[:1]), ('CIVIC_CODE_OF_LAWS', ids[1:2]), ('DISTRICT_CAMPUS', ids[2:3]), ('RESOLUTION_TEST', ids[3:])]:
        actual = cli(root, 'show', ident, '--level', 'evidence')['code_evidence']
        assert [entry['id'] for entry in actual] == expected


def test_built_directory_contains_readable_guides_and_standalone_commands(tmp_path):
    import re
    root = build(tmp_path)
    for name in ['README.md', 'NEXT.md', 'capture.py', 'build.py', 'query.py']:
        assert (root / name).is_file(), name
    topics = cli(root, 'topics')['topics']
    for topic in topics:
        guide = root / topic['guide']
        assert guide.is_file()
        for link in re.findall(r'\]\(([^)]+)\)', guide.read_text()):
            if '://' not in link and not link.startswith('#'):
                assert (guide.parent / link.split('#')[0]).exists(), link
    result = subprocess.run([sys.executable, str(root / 'query.py'), 'validate'], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    for script in ['capture.py', 'build.py']:
        result = subprocess.run([sys.executable, str(root / script), '--help'], capture_output=True, text=True, cwd=tmp_path)
        assert result.returncode == 0, result.stderr


def test_profile_must_be_an_object_before_comparing_fields(tmp_path):
    root = build(tmp_path)
    profile = tmp_path / 'malformed-profile.json'
    for value in [[], {'profile': []}]:
        profile.write_text(json.dumps(value))
        result = cli(root, 'check-profile', str(profile), ok=False)
        assert 'profile must be an object' in result['error']
        result = cli(root, '--profile', str(profile), 'topics', ok=False)
        assert 'profile must be an object' in result['error']


def test_missing_core_relationship_tables_cannot_build_a_complete_looking_tree(tmp_path):
    for table in ['TechnologyPrereqs', 'CivicPrereqs', 'Boosts', 'District_Adjacencies', 'Adjacency_YieldChanges']:
        data = snapshot()
        del data['tables'][table]
        source = tmp_path / 'incomplete.json'
        source.write_text(json.dumps(data))
        output = tmp_path / 'incomplete'
        result = subprocess.run([sys.executable, str(legacy_distribution(tmp_path) / 'build.py'), '--source', str(source), '--output', str(output)], capture_output=True, text=True)
        assert result.returncode == 2, table
        assert 'missing core relationship table' in json.loads(result.stdout)['error']
        assert not output.exists()


def test_missing_optional_coverage_is_derived_from_actual_tables(tmp_path):
    data = snapshot()
    data['profile']['missing_optional_tables'] = []
    root = build(tmp_path, data)
    assert 'ResolutionEffects' in cli(root, 'validate')['coverage']['missing_optional_tables']


def test_malformed_snapshot_and_manifest_roots_fail_as_structured_errors(tmp_path):
    source = tmp_path / 'array.json'
    source.write_text('[]')
    result = subprocess.run([sys.executable, str(legacy_distribution(tmp_path) / 'build.py'), '--source', str(source), '--output', str(tmp_path / 'bad-root')], capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert 'snapshot must be an object' in json.loads(result.stdout)['error']
    root = build(tmp_path)
    (root / 'manifest.json').write_text('[]')
    assert 'manifest must be an object' in cli(root, 'validate', ok=False)['error']


def test_incomplete_support_distribution_refuses_to_build_and_missing_guide_is_invalid(tmp_path):
    root = build(tmp_path)
    (root / 'topics/civics.md').unlink()
    assert cli(root, 'validate', ok=False)['status'] == 'error'
    output = tmp_path / 'must-not-exist'
    result = subprocess.run([sys.executable, str(root / 'build.py'), '--source', str(root / 'sources/snapshot.json'), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'missing support file' in json.loads(result.stdout)['error']
    assert not output.exists()
