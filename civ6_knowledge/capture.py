"""Capture a portable rules snapshot from explicit, read-only local game inputs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import plistlib
import re
import sqlite3
import shutil
import tempfile
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from build import TOPIC_NAMES

PRIMARY_TABLES = ('Technologies', 'Civics', 'Districts', 'Resolutions', 'ProposalTypes', 'VotingBlockers')
TABLES = PRIMARY_TABLES + (
    'TechnologyPrereqs', 'CivicPrereqs', 'Boosts', 'Districts_XP2',
    'District_Adjacencies', 'Adjacency_YieldChanges', 'District_RequiredFeatures',
    'District_ValidTerrains', 'DistrictReplaces', 'District_CitizenYieldChanges',
    'Buildings', 'Units', 'Improvements', 'Projects', 'Policies', 'Governments',
    'Resources', 'Routes', 'GovernmentSlots', 'Government_SlotCounts',
    'ResolutionEffects', 'CivilizationTraits', 'LeaderTraits',
    'CivilopediaPages', 'CivilopediaPageChapterParagraphs', 'GlobalParameters',
)
TABLES = tuple(dict.fromkeys(TABLES + (
    'Civilizations', 'Leaders', 'CivilizationLeaders', 'Traits', 'TraitModifiers', 'BuildingReplaces', 'ImprovementModifiers',
    'Happinesses', 'PolicyModifiers',
    'Yields',
    'BuildingModifiers',
    'Building_YieldChanges',
    'Building_CitizenYieldChanges',
    'BuildingPrereqs',
    'Building_ValidTerrains',
    'Building_RequiredFeatures',
    'Building_ResourceCosts',
    'Building_YieldChangesBonusWithPower',
    'Buildings_XP2',
    'Improvement_YieldChanges',
    'Improvement_ValidResources',
    'Resource_Consumption',
    'Resource_YieldChanges',
    'Resource_Harvests',
    'Resource_ValidTerrains',
    'Resource_ValidFeatures',
    'Features_XP2',
    'Feature_Floodplains',
    'RandomEvents',
    'RandomEvent_Damages',
    'RandomEvent_Yields',
    'RandomEvent_Terrains',
    'RandomEvent_Features',
    'RandomEvent_Frequencies',
    'RandomEvent_PillagedBuildings',
    'RandomEvent_PillagedDistricts',
    'RandomEvent_PillagedImprovements',
    'RandomEvent_DamagedUnits',
    'Units_XP2',
    'UnitPromotions',
    'UnitPromotionPrereqs',
    'UnitUpgrades',
    'Unit_BuildingPrereqs',
    'UnitReplaces',
    'UnitAbilities',
    'UnitAbilityModifiers',
    'UnitPromotionModifiers',
    'TypeTags',
    'Tags',
    'Terrains',
    'Terrain_YieldChanges',
    'Features',
    'Feature_YieldChanges',
    'Feature_UnitMovements',
    'DiplomaticActions',
    'Alliances',
    'AllianceEffects',
    'DiplomaticStates',
    'DiplomaticStateActions',
    'DiplomaticActions_XP1',
    'DiplomaticActions_XP2',
    'DiplomaticVisibilitySources',
    'DiplomaticVisibilitySources_XP1',
    'District_TradeRouteYields',
    'Resource_TradeRouteYields',
    'Victories',
    'Religions',
    'Beliefs',
    'BeliefClasses',
    'BeliefModifiers',
    'Projects_XP1',
    'Projects_XP2',
    'Project_YieldConversions',
    'Project_ResourceCosts',
    'Modifiers',
    'ModifierArguments',
    'DynamicModifiers',
    'Requirements',
    'RequirementArguments',
    'RequirementSets',
    'RequirementSetRequirements',
    'GameSpeeds',
    'GameSpeed_Turns',
)))
# Small native excerpts explain conditions that database fields alone cannot express.
CODE_RANGES = (
    ('technology-live-prerequisites', 'Assets/Base/Assets/UI/Civilopedia/CivilopediaPage_Technology.lua', 32, 62),
    ('civic-live-prerequisites', 'Assets/Base/Assets/UI/Civilopedia/CivilopediaPage_Civic.lua', 34, 65),
    ('district-adjacency-calculation', 'Assets/Base/Assets/UI/Civilopedia/CivilopediaPage_District.lua', 235, 307),
    ('congress-voting-blockers', 'Assets/DLC/Expansion2/UI/Additions/WorldCongressPopup.lua', 1127, 1155),
    ('congress-submit-eligibility', 'Assets/DLC/Expansion2/UI/Additions/WorldCongressPopup.lua', 1238, 1265),
    ('congress-intro-open', 'Assets/DLC/Expansion2/UI/Additions/WorldCongressIntro.lua', 20, 40),
    ('congress-stage-flow', 'Assets/DLC/Expansion2/UI/Additions/WorldCongressIntro.lua', 75, 145),
)

# Retain conditional XML records, not a simulated DLC/mod loader. These records
# never alter the cached tables or attest ownership, activation or final values.
INSTALLED_TABLES = {
    'Civilizations', 'Leaders', 'Traits', 'CivilizationTraits', 'LeaderTraits', 'CivilizationLeaders',
    'TraitModifiers', 'Units', 'Buildings', 'Districts', 'Improvements',
    'UnitReplaces', 'BuildingReplaces', 'DistrictReplaces', 'BuildingModifiers', 'ImprovementModifiers',
    'Building_YieldChanges', 'Improvement_YieldChanges', 'UnitAbilities', 'UnitAbilityModifiers', 'TypeTags',
    'Modifiers', 'ModifierArguments', 'DynamicModifiers', 'Requirements', 'RequirementArguments',
    'RequirementSets', 'RequirementSetRequirements',
}


def xml_fields(node):
    return dict(node.attrib) | {child.tag: ''.join(child.itertext()) for child in node}


def installed_inputs(game_root):
    inputs, rows, gaps, text_inputs, captured_hashes = {}, [], [], [], {}
    for manifest in sorted((game_root / 'Assets/DLC').glob('*/*.modinfo')):
        logical_manifest = 'game/Contents/' + str(manifest.relative_to(game_root))
        inputs[logical_manifest] = (manifest, logical_manifest)
        captured_hashes[logical_manifest] = digest(manifest)
        root = ET.parse(manifest).getroot()
        criteria = {node.get('id'): ET.tostring(node, encoding='unicode') for node in root.findall('ActionCriteria/Criteria')}
        for action in root.findall('InGameActions/*'):
            if action.tag not in ('UpdateDatabase', 'UpdateText'):
                continue
            for entry in action.findall('File'):
                relative = (entry.text or '').strip()
                file = (manifest.parent / relative).resolve()
                if not file.is_relative_to(game_root.resolve()) or not file.is_file():
                    gaps.append({'path': relative, 'package': root.get('id'), 'manifest': logical_manifest,
                                 'action': action.get('id'), 'criteria': action.get('criteria'), 'status': 'missing_or_external_input'})
                    continue
                logical = 'game/Contents/' + str(file.relative_to(game_root.resolve()))
                inputs[logical] = (file, logical)
                captured_hashes.setdefault(logical, digest(file))
                if action.tag == 'UpdateText':
                    if file.suffix.lower() == '.xml':
                        text_inputs.append((file, logical))
                    continue
                context = {'package': root.get('id'), 'manifest': logical_manifest,
                           'path': logical, 'action': action.get('id'), 'criteria': action.get('criteria'),
                           'criteria_definition': criteria.get(action.get('criteria')),
                           'action_properties': ET.tostring(action.find('Properties'), encoding='unicode') if action.find('Properties') is not None else None,
                           'scope': 'installed_definition_catalog'}
                if file.suffix.lower() != '.xml':
                    gaps.append(context | {'status': 'unsupported_sql_not_applied' if file.suffix.lower() == '.sql' else 'unsupported_format_not_applied'})
                    continue
                for table in ET.parse(file).getroot():
                    if table.tag not in INSTALLED_TABLES:
                        continue
                    for index, operation in enumerate(table):
                        facts = xml_fields(operation.find('Set')) if operation.tag == 'Update' and operation.find('Set') is not None else xml_fields(operation)
                        where = xml_fields(operation.find('Where')) if operation.find('Where') is not None else {}
                        rows.append(context | {'table': table.tag, 'operation': operation.tag, 'row': index,
                                                'facts': facts, 'where': where,
                                                'raw_xml': ET.tostring(operation, encoding='unicode')})
    return inputs, ordered(rows), ordered(gaps), sorted(set(text_inputs)), captured_hashes


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordered(rows):
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))


@contextmanager
def copied_database(path: Path):
    # SQLite read-only connections may create SHM beside a WAL database. Only
    # connect to isolated copies; copy WAL too so committed rows remain visible.
    with tempfile.TemporaryDirectory(prefix='civ6-knowledge-') as directory:
        copied = Path(directory) / path.name
        shutil.copyfile(path, copied)
        wal = Path(str(path) + '-wal')
        if wal.exists():
            shutil.copyfile(wal, Path(str(copied) + '-wal'))
        conn = sqlite3.connect(copied.as_uri() + '?mode=ro', uri=True)
        try:
            yield conn
        finally:
            conn.close()


def read_tables(path: Path) -> tuple[dict, list]:
    with copied_database(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        available = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(PRIMARY_TABLES) - available
        if missing:
            raise ValueError(f'missing required rules tables: {sorted(missing)}')
        tables = {}
        for name in TABLES:
            if name not in available:
                continue
            rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
            tables[name] = ordered(rows)
        return tables, sorted(set(TABLES) - available)


def read_text(path: Path, tables: dict, xml_inputs: list) -> tuple[dict, dict]:
    tags = set(re.findall(r'LOC_[A-Za-z0-9_]+', json.dumps(tables)))
    prefixes = tuple('LOC_PEDIA_CONCEPTS_PAGE_' + row['PageId'] + '_' for row in tables.get('CivilopediaPages', []) if row.get('SectionId') == 'CONCEPTS')
    result, origins = {}, {}
    def wanted(tag):
        return tag in tags or bool(prefixes and tag.startswith(prefixes))
    with copied_database(path) as conn:
        for language, tag, text in conn.execute(
                "SELECT Language,Tag,Text FROM LocalizedText WHERE Language IN ('zh_Hans_CN','en_US') ORDER BY Language,Tag"):
            if wanted(tag):
                result.setdefault(tag, {})[language] = text
                origins.setdefault(tag, {})[language] = 'user/Cache/DebugLocalization.sqlite'
    # The cached localization DB may lack expansion-only tags. Fill missing
    # literals from installed Expansion2 text, without overriding cached entries.
    # BaseGameText is an English source group too; configuration translations
    # supply some in-game names, such as Diplomatic Victory.
    for file, logical_path in xml_inputs:
        document = ET.parse(file)
        for group in document.getroot():
            default = 'en_US' if group.tag in ('EnglishText', 'BaseGameText') else None
            for row in group:
                language = row.get('Language', default)
                tag = row.get('Tag')
                text = row.find('Text')
                # Additional DLC text supplies referenced civilization literals;
                # keep the existing concept-body corpus stable across packages.
                relevant = tag in tags or ('/Expansion2/Text/' in logical_path and wanted(tag))
                if tag and language in ('en_US', 'zh_Hans_CN') and text is not None and relevant and language not in result.get(tag, {}):
                    result.setdefault(tag, {})[language] = ''.join(text.itertext())
                    origins.setdefault(tag, {})[language] = logical_path
    return dict(sorted(result.items())), origins


def startup_version(path: Path) -> tuple[str, datetime]:
    lines = path.read_text().splitlines()
    versions = [(index, re.search(r'Game Version\s*=\s*(.+)', line))
                for index, line in enumerate(lines) if 'Game Version' in line]
    versions = [(index, match) for index, match in versions if match]
    if not versions:
        raise ValueError('missing game version evidence')
    index, version = versions[-1]
    boots = [i for i, line in enumerate(lines) if 'Civ6App::InitialInit start.' in line]
    if boots and boots[-1] > index:
        raise ValueError('latest startup has no game version evidence')

    def timestamp(line):
        match = re.match(r'^\[([^]]+)\]', line)
        if not match:
            raise ValueError('missing startup timestamp')
        try:
            return datetime.strptime(match[1], '%Y-%m-%d %H:%M:%S')
        except ValueError as exc:
            raise ValueError('invalid startup timestamp') from exc

    # Logs can be appended at shutdown; file mtime is not a boot timestamp.
    started = timestamp(lines[index])
    previous = versions[-2][0] if len(versions) > 1 else -1
    matching_boots = [i for i in boots if previous < i <= index]
    if matching_boots:
        started = timestamp(lines[matching_boots[-1]])
    return version[1].strip(), started


def capture(game_root: Path, user_root: Path, steam_manifest: Path) -> dict:
    game_root = game_root.resolve()
    inputs = {
        'bundle': (game_root / 'Info.plist', 'game/Contents/Info.plist'),
        'steam': (steam_manifest, 'steam/appmanifest_289070.acf'),
        'startup': (user_root / 'Logs/Startup.log', 'user/Logs/Startup.log'),
        'modding': (user_root / 'Logs/Modding.log', 'user/Logs/Modding.log'),
        'gameplay': (user_root / 'Cache/DebugGameplay.sqlite', 'user/Cache/DebugGameplay.sqlite'),
        'localization': (user_root / 'Cache/DebugLocalization.sqlite', 'user/Cache/DebugLocalization.sqlite'),
    }
    for _, relative, start, end in CODE_RANGES:
        p = game_root / relative
        if p.is_file():
            inputs[relative] = (p, 'game/Contents/' + relative)
    text_root = game_root / 'Assets/DLC/Expansion2/Text'
    xml_paths = sorted((text_root / 'en_US').glob('*.xml')) + [p for p in [text_root / 'Expansion2_Translations_Text.xml', text_root / 'Expansion2_Translations_ConfigText.xml'] if p.is_file()]
    xml_inputs = [(p, 'game/Contents/' + str(p.relative_to(game_root))) for p in xml_paths]
    installed_files, installed_rows, installed_gaps, installed_text, installed_hashes = installed_inputs(game_root)
    inputs.update(installed_files)
    # Preserve the established Expansion2 fallback before supplementing other
    # packages; installation order must not silently replace existing literals.
    xml_inputs = list(dict.fromkeys(xml_inputs + installed_text))
    for p, logical in xml_inputs:
        inputs[logical] = (p, logical)
    wal_states = {name: Path(str(inputs[name][0]) + '-wal').exists() for name in ('gameplay', 'localization')}
    for name in ('gameplay', 'localization'):
        wal = Path(str(inputs[name][0]) + '-wal')
        if wal_states[name]:
            inputs[name + '_wal'] = (wal, inputs[name][1] + '-wal')
    hashes = {name: digest(p) for name, (p, _) in inputs.items()}
    if any(hashes[name] != fingerprint for name, fingerprint in installed_hashes.items()):
        raise ValueError('installed source changed during capture; keep prior snapshot')
    bundle = plistlib.loads(inputs['bundle'][0].read_bytes())
    version, started = startup_version(inputs['startup'][0])
    if inputs['gameplay'][0].stat().st_mtime < started.timestamp():
        raise ValueError('gameplay cache is older than startup; source version binding is stale')
    build = re.search(r'"buildid"\s*"([0-9]+)"', steam_manifest.read_text())
    if not version or not build or not bundle.get('CFBundleShortVersionString') or not bundle.get('CFBundleVersion'):
        raise ValueError('missing game, bundle or Steam version evidence')
    tables, missing = read_tables(inputs['gameplay'][0])
    tables['InstalledRuleRows'] = installed_rows
    profile = {
        'game_version': version,
        'startup_started_at': started.isoformat(),
        'startup_timestamp_basis': 'startup_log_local_time',
        'macos_bundle_version': bundle['CFBundleShortVersionString'],
        'macos_bundle_build': bundle['CFBundleVersion'],
        'steam_build_id': build[1],
        'ruleset': 'RULESET_EXPANSION_2',
        'ruleset_basis': 'target knowledge ruleset; Expansion2 definitions required; not a loaded-session attestation',
        'source_scope': 'local_cached_rules_snapshot',
        'current_session_verified': False,
        'active_mods_verified': False,
        'active_modes_verified': False,
        'language_preference': ['zh_Hans_CN', 'en_US'],
        'missing_optional_tables': missing,
    }
    lines = inputs['modding'][0].read_text(errors='replace').splitlines()
    start = next((i for i in range(len(lines) - 1, -1, -1) if 'Enabled Mods (in no particular order)' in lines[i]), None)
    mods = []
    if start is not None:
        for line in lines[start + 1:]:
            match = re.search(r'\]\s+([0-9a-fA-F-]{36})\s+\(([^\r\n]*)\)', line)
            if not match:
                break
            mods.append({'id': match[1].lower(), 'label': match[2]})
    profile['configuration_enabled_mods'] = sorted(mods, key=lambda r: r['id'])
    profile['configuration_warning'] = 'Configuration enabled lists do not prove per-game active modes or ownership.'
    profile['installed_catalog_gaps'] = installed_gaps
    profile['installed_catalog_policy'] = 'Conditional XML records only; no SQL, updates, criteria or load order evaluated; never merged into cached rules; ownership and activation unknown.'
    evidence = []
    for name, relative, first, last in CODE_RANGES:
        if relative in inputs:
            content = inputs[relative][0].read_text().splitlines()
            evidence.append({'id': name, 'path': inputs[relative][1], 'line_start': first,
                             'line_end': min(last, len(content)), 'sha256': hashes[relative],
                             'text': '\n'.join(content[first - 1:last]),
                             'text_sha256': hashlib.sha256('\n'.join(content[first - 1:last]).encode()).hexdigest()})
    localized, origins = read_text(inputs['localization'][0], tables, xml_inputs)
    tables['CivilopediaTexts'] = [{'Tag': tag, 'Language': language, 'Text': text, 'Source': origins[tag][language]} for tag, texts in localized.items() if tag.startswith('LOC_PEDIA_CONCEPTS_PAGE_') for language, text in sorted(texts.items())]
    profile['derived_tables'] = ['CivilopediaTexts', 'InstalledRuleRows']
    profile['localization_policy'] = 'Cached text; missing literals supplemented first from installed Expansion2 text, then referenced installed package text. Active overrides and package load order unverified.'
    wal_changed = any(Path(str(inputs[name][0]) + '-wal').exists() != present for name, present in wal_states.items())
    if wal_changed or any(digest(p) != hashes[name] for name, (p, _) in inputs.items()):
        raise ValueError('source changed during capture; keep prior snapshot and recapture after files stabilize')
    return {'schema_version': 1, 'knowledge_topics': list(TOPIC_NAMES), 'profile': profile, 'tables': tables, 'localization': localized, 'localization_sources': origins,
            'code_evidence': evidence, 'sources': [
                {'id': name, 'path': relative, 'sha256': hashes[name], 'size_bytes': p.stat().st_size}
                for name, (p, relative) in sorted(inputs.items())]}


def main() -> int:
    home = Path.home()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game-root', type=Path, default=home / "Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents")
    p.add_argument('--user-root', type=Path, default=home / "Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI")
    p.add_argument('--steam-manifest', type=Path, default=home / 'Library/Application Support/Steam/steamapps/appmanifest_289070.acf')
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'sources/snapshot.json')
    args = p.parse_args()
    try:
        output = args.output.resolve()
        for root in (args.game_root.resolve(), args.user_root.resolve()):
            if output.is_relative_to(root):
                raise ValueError('output must be outside game and user data directories')
        if output == args.steam_manifest.resolve():
            raise ValueError('output cannot replace Steam manifest')
        data = capture(args.game_root, args.user_root, args.steam_manifest)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        temp.replace(output)
        print(json.dumps({'status': 'captured', 'tables': len(data['tables']), 'game_version': data['profile']['game_version']}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error, plistlib.InvalidFileException, ET.ParseError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
