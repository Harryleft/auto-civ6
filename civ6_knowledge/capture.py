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
from pathlib import Path

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
            if name.startswith('Civilopedia'):
                rows = [r for r in rows if r.get('SectionId') in {'CIVICS', 'TECHNOLOGIES', 'DISTRICTS'}
                        or r.get('PageId', '').startswith(('SCIENCE_', 'CULTURE_', 'GOVT_', 'CITIES_', 'WORLD_CONGRESS', 'DIPLOMATIC_VICTORY'))]
            if name == 'GlobalParameters':
                rows = [r for r in rows if r.get('Name', '').startswith(('DISTRICT_POPULATION_', 'WORLD_CONGRESS_', 'DIPLOMATIC_VICTORY_'))]
            tables[name] = ordered(rows)
        return tables, sorted(set(TABLES) - available)


def read_text(path: Path, tables: dict) -> dict:
    tags = set(re.findall(r'LOC_[A-Za-z0-9_]+', json.dumps(tables)))
    result = {}
    with copied_database(path) as conn:
        for language, tag, text in conn.execute(
                "SELECT Language,Tag,Text FROM LocalizedText WHERE Language IN ('zh_Hans_CN','en_US') ORDER BY Language,Tag"):
            if tag in tags:
                result.setdefault(tag, {})[language] = text
    return dict(sorted(result.items()))


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
    wal_states = {name: Path(str(inputs[name][0]) + '-wal').exists() for name in ('gameplay', 'localization')}
    for name in ('gameplay', 'localization'):
        wal = Path(str(inputs[name][0]) + '-wal')
        if wal_states[name]:
            inputs[name + '_wal'] = (wal, inputs[name][1] + '-wal')
    hashes = {name: digest(p) for name, (p, _) in inputs.items()}
    bundle = plistlib.loads(inputs['bundle'][0].read_bytes())
    version, started = startup_version(inputs['startup'][0])
    if inputs['gameplay'][0].stat().st_mtime < started.timestamp():
        raise ValueError('gameplay cache is older than startup; source version binding is stale')
    build = re.search(r'"buildid"\s*"([0-9]+)"', steam_manifest.read_text())
    if not version or not build or not bundle.get('CFBundleShortVersionString') or not bundle.get('CFBundleVersion'):
        raise ValueError('missing game, bundle or Steam version evidence')
    tables, missing = read_tables(inputs['gameplay'][0])
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
    evidence = []
    for name, relative, first, last in CODE_RANGES:
        if relative in inputs:
            content = inputs[relative][0].read_text().splitlines()
            evidence.append({'id': name, 'path': inputs[relative][1], 'line_start': first,
                             'line_end': min(last, len(content)), 'sha256': hashes[relative],
                             'text': '\n'.join(content[first - 1:last]),
                             'text_sha256': hashlib.sha256('\n'.join(content[first - 1:last]).encode()).hexdigest()})
    localized = read_text(inputs['localization'][0], tables)
    wal_changed = any(Path(str(inputs[name][0]) + '-wal').exists() != present for name, present in wal_states.items())
    if wal_changed or any(digest(p) != hashes[name] for name, (p, _) in inputs.items()):
        raise ValueError('source changed during capture; keep prior snapshot and recapture after files stabilize')
    return {'schema_version': 1, 'profile': profile, 'tables': tables, 'localization': localized,
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
    except (OSError, ValueError, sqlite3.Error, plistlib.InvalidFileException) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
