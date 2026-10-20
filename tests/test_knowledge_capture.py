"""Public offline capture CLI; all game inputs are isolated temporary files."""
from datetime import datetime
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'civ6_knowledge' / 'capture.py'


def setup_inputs(tmp_path):
    game = tmp_path / 'Contents'
    game.mkdir()
    (game / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleShortVersionString': '1.4.6', 'CFBundleVersion': '376653.178',
    }))
    user = tmp_path / 'user'
    (user / 'Cache').mkdir(parents=True)
    (user / 'Logs').mkdir()
    started = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    (user / 'Logs/Startup.log').write_text(f'[{started}]\tGame Version = 1.0.12.54 (951533)\n')
    (user / 'Logs/Modding.log').write_text('Enabled Mods (in no particular order):\n[123.0] 4873eb62-8ccc-4574-b784-dda455e74e68 (Expansion2)\nnext\n')
    manifest = tmp_path / 'appmanifest.acf'
    manifest.write_text('"buildid" "15296837"\n"LastOwner" "private-account"')
    with sqlite3.connect(user / 'Cache/DebugGameplay.sqlite') as con:
        for table, key in [('Technologies', 'TechnologyType'), ('Civics', 'CivicType'),
                           ('Districts', 'DistrictType'), ('Resolutions', 'ResolutionType'),
                           ('ProposalTypes', 'ProposalType'), ('VotingBlockers', 'VotingBlockerType')]:
            con.execute(f'CREATE TABLE {table} ({key} TEXT, Name TEXT)')
        con.execute("INSERT INTO Technologies VALUES ('TECH_WRITING','LOC_WRITING')")
    con.close()
    with sqlite3.connect(user / 'Cache/DebugLocalization.sqlite') as con:
        con.execute('CREATE TABLE LocalizedText (Language TEXT,Tag TEXT,Text TEXT)')
        con.executemany('INSERT INTO LocalizedText VALUES (?,?,?)', [
            ('zh_Hans_CN', 'LOC_WRITING', '写作'), ('en_US', 'LOC_WRITING', 'Writing'),
            ('en_US', 'LOC_UNRELATED', 'unrelated'),
        ])
    con.close()
    return game, user, manifest


def capture(tmp_path, game, user, manifest):
    output = tmp_path / 'knowledge/sources/snapshot.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--game-root', str(game),
                             '--user-root', str(user), '--steam-manifest', str(manifest),
                             '--output', str(output)], text=True, capture_output=True)
    return result, output


@pytest.mark.skipif(sys.platform != 'win32', reason='验证 Windows 默认代码页下的文件编码')
def test_capture_writes_utf8_without_inherited_utf8_mode(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    output = tmp_path / 'knowledge/sources/snapshot.json'
    env = dict(os.environ, PYTHONUTF8='0')
    result = subprocess.run(
        [sys.executable, '-X', 'utf8=0', str(SCRIPT), '--game-root', str(game),
         '--user-root', str(user), '--steam-manifest', str(manifest), '--output', str(output)],
        stdin=subprocess.DEVNULL, capture_output=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_bytes().decode('utf-8'))
    assert data['localization']['LOC_WRITING']['zh_Hans_CN'] == '写作'


def test_capture_is_portable_versioned_and_keeps_raw_facts(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data['profile']['game_version'] == '1.0.12.54 (951533)'
    assert data['profile']['steam_build_id'] == '15296837'
    assert data['profile']['ruleset'] == 'RULESET_EXPANSION_2'
    assert data['profile']['current_session_verified'] is False
    assert data['profile']['active_modes_verified'] is False
    assert data['tables']['Technologies'][0]['TechnologyType'] == 'TECH_WRITING'
    assert data['localization']['LOC_WRITING']['zh_Hans_CN'] == '写作'
    assert 'LOC_UNRELATED' not in data['localization']
    assert str(tmp_path) not in output.read_text()
    assert 'private-account' not in output.read_text()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_capture_refuses_cache_older_than_startup_and_preserves_previous_output(tmp_path):
    import os
    game, user, manifest = setup_inputs(tmp_path)
    cache = user / 'Cache/DebugGameplay.sqlite'
    old = (user / 'Logs/Startup.log').stat().st_mtime - 60
    os.utime(cache, (old, old))
    output = tmp_path / 'knowledge/sources/snapshot.json'
    output.parent.mkdir(parents=True)
    output.write_text('previous confirmed snapshot')
    result, _ = capture(tmp_path, game, user, manifest)
    assert result.returncode == 2
    assert 'older than startup' in result.stderr
    assert output.read_text() == 'previous confirmed snapshot'


def test_capture_missing_database_never_creates_or_replaces_game_files(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    database = user / 'Cache/DebugGameplay.sqlite'
    database.unlink()
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 2
    assert not database.exists()
    assert not output.exists()


def test_capture_rejects_output_inside_game_data(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    original = game / 'Info.plist'
    before = original.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), '--game-root', str(game),
                             '--user-root', str(user), '--steam-manifest', str(manifest),
                             '--output', str(original)], text=True, capture_output=True)
    assert result.returncode == 2
    assert original.read_bytes() == before


def test_capture_reads_committed_wal_without_creating_source_sidecars(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    database = user / 'Cache/DebugGameplay.sqlite'
    # Preserve a database plus committed WAL without SHM, as produced by a copied
    # SQLite cache. Opening this original in mode=ro would create a source SHM.
    con = sqlite3.connect(database)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA wal_autocheckpoint=0')
    con.execute("INSERT INTO Technologies VALUES ('TECH_CURRENCY','LOC_CURRENCY')")
    con.commit()
    db_bytes = database.read_bytes()
    wal_bytes = Path(str(database) + '-wal').read_bytes()
    con.close()
    database.write_bytes(db_bytes)
    Path(str(database) + '-wal').write_bytes(wal_bytes)
    assert not Path(str(database) + '-shm').exists()
    before = {str(p.relative_to(user)): p.read_bytes() for p in user.rglob('*') if p.is_file()}
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    after = {str(p.relative_to(user)): p.read_bytes() for p in user.rglob('*') if p.is_file()}
    assert before == after
    rows = json.loads(output.read_text())['tables']['Technologies']
    assert any(row['TechnologyType'] == 'TECH_CURRENCY' for row in rows)


def test_capture_uses_last_boot_marker_instead_of_exit_appended_file_mtime(tmp_path):
    import os
    game, user, manifest = setup_inputs(tmp_path)
    started = datetime(2026, 10, 17, 19, 45, 1)
    startup = user / 'Logs/Startup.log'
    startup.write_text('[2026-10-16 10:00:00]\tGame Version = old-version\n'
                       '[2026-10-17 19:45:01]\tCiv6App::InitialInit start.\n'
                       '[2026-10-17 19:45:01]\tGame Version = 1.0.12.54 (951533)\n'
                       '[2026-10-17 23:40:00]\tQueue counters at exit\n')
    cache = user / 'Cache/DebugGameplay.sqlite'
    os.utime(cache, (started.timestamp() + 3600, started.timestamp() + 3600))
    os.utime(startup, (started.timestamp() + 7200, started.timestamp() + 7200))
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    profile = json.loads(output.read_text())['profile']
    assert profile['game_version'] == '1.0.12.54 (951533)'
    assert profile['startup_started_at'] == '2026-10-17T19:45:01'
    assert profile['current_session_verified'] is False


def test_missing_or_invalid_startup_timestamp_preserves_previous_snapshot(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    output = tmp_path / 'knowledge/sources/snapshot.json'
    output.parent.mkdir(parents=True)
    output.write_text('previous confirmed snapshot')
    for line in ['Game Version = 1.0.12.54 (951533)', '[not-a-time] Game Version = 1.0.12.54 (951533)']:
        (user / 'Logs/Startup.log').write_text(line + '\n')
        result, _ = capture(tmp_path, game, user, manifest)
        assert result.returncode == 2
        assert 'startup timestamp' in result.stderr
        assert output.read_text() == 'previous confirmed snapshot'


def test_capture_expanded_theme_facts_without_filtering_their_mechanisms(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    with sqlite3.connect(user / 'Cache/DebugGameplay.sqlite') as con:
        con.execute('CREATE TABLE Yields (YieldType TEXT, Name TEXT)')
        con.execute("INSERT INTO Yields VALUES ('YIELD_FOOD','LOC_FOOD')")
        con.execute('CREATE TABLE Resource_Consumption (ResourceType TEXT, PowerProvided INTEGER)')
        con.execute("INSERT INTO Resource_Consumption VALUES ('RESOURCE_COAL',4)")
        con.execute('CREATE TABLE GlobalParameters (Name TEXT, Value TEXT)')
        con.execute("INSERT INTO GlobalParameters VALUES ('CITY_GROWTH_THRESHOLD','15')")
        con.execute('CREATE TABLE CivilopediaPages (SectionId TEXT, PageId TEXT, Name TEXT)')
        con.execute("INSERT INTO CivilopediaPages VALUES ('CONCEPTS','FAITH_1','LOC_FAITH')")
    with sqlite3.connect(user / 'Cache/DebugLocalization.sqlite') as con:
        con.execute("INSERT INTO LocalizedText VALUES ('zh_Hans_CN','LOC_FOOD','食物')")
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data['tables']['Yields'][0]['YieldType'] == 'YIELD_FOOD'
    assert data['tables']['Resource_Consumption'][0]['PowerProvided'] == 4
    assert data['tables']['GlobalParameters'][0]['Name'] == 'CITY_GROWTH_THRESHOLD'
    assert data['tables']['CivilopediaPages'][0]['PageId'] == 'FAITH_1'
    assert data['localization']['LOC_FOOD']['zh_Hans_CN'] == '食物'


def test_capture_concept_bodies_and_missing_expansion_text_have_sources(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    with sqlite3.connect(user / 'Cache/DebugGameplay.sqlite') as con:
        con.execute('CREATE TABLE CivilopediaPages (SectionId TEXT, PageId TEXT, Name TEXT)')
        con.execute("INSERT INTO CivilopediaPages VALUES ('CONCEPTS','MOVEMENT_1','LOC_MOVE_TITLE')")
        con.execute('CREATE TABLE Units (UnitType TEXT, Description TEXT)')
        con.execute("INSERT INTO Units VALUES ('UNIT_TEST','LOC_UNIT_TEST_GS')")
    with sqlite3.connect(user / 'Cache/DebugLocalization.sqlite') as con:
        con.execute("INSERT INTO LocalizedText VALUES ('en_US','LOC_PEDIA_CONCEPTS_PAGE_MOVEMENT_1_CHAPTER_CONTENT_PARA_1','Every unit has movement points.')")
    text = game / 'Assets/DLC/Expansion2/Text/en_US/Expansion2_Units_Text.xml'
    text.parent.mkdir(parents=True)
    text.write_text('<GameData><EnglishText><Row Tag="LOC_UNIT_TEST_GS"><Text>GS-specific ability</Text></Row></EnglishText></GameData>')
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    bodies = data['tables']['CivilopediaTexts']
    assert bodies[0]['Text'] == 'Every unit has movement points.'
    assert data['localization']['LOC_UNIT_TEST_GS']['en_US'] == 'GS-specific ability'
    assert data['localization_sources']['LOC_UNIT_TEST_GS']['en_US'].endswith('Expansion2_Units_Text.xml')
