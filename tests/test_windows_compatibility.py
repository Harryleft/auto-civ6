"""离线覆盖 Windows 注册表路径、默认编码和无输入的知识子进程。"""

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from civ_mcp import game_launcher, knowledge, platform_paths, version
from test_knowledge_queries import build


def test_windows_uses_redirected_documents_for_saves(monkeypatch, tmp_path):
    monkeypatch.delenv('CIV6_SAVE_DIR', raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    documents = tmp_path / 'OneDrive' / '文档'
    monkeypatch.setattr(platform_paths, 'windows_registry_path', lambda *args: str(documents))
    assert platform_paths.single_save_directory() == str(
        documents / "My Games/Sid Meier's Civilization VI/Saves/Single"
    )


def test_windows_falls_back_to_documents_without_registry(monkeypatch):
    monkeypatch.delenv('CIV6_SAVE_DIR', raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(platform_paths, 'windows_registry_path', lambda *args: None)
    assert platform_paths.single_save_directory() == str(
        Path.home() / "Documents/My Games/Sid Meier's Civilization VI/Saves/Single"
    )


def test_save_directory_can_be_overridden_without_touching_disk(monkeypatch, tmp_path):
    target = tmp_path / '中文存档' / 'Single'
    monkeypatch.setenv('CIV6_SAVE_DIR', str(target))
    assert platform_paths.single_save_directory() == str(target)
    assert not target.exists()


@pytest.mark.parametrize('value', ['', 'relative/Single'])
def test_invalid_save_override_is_explicit(monkeypatch, value):
    monkeypatch.setenv('CIV6_SAVE_DIR', value)
    with pytest.raises(ValueError, match='absolute'):
        platform_paths.single_save_directory()


@pytest.mark.parametrize('has_vdf', [False, True])
def test_steam_registry_supports_nondefault_drive_and_external_library(monkeypatch, tmp_path, has_vdf):
    monkeypatch.setattr(sys, 'platform', 'win32')
    steam = tmp_path / 'Steam on S'
    library = tmp_path / 'SteamLibrary on D' if has_vdf else steam
    monkeypatch.setattr(platform_paths, 'windows_registry_path', lambda *args: str(steam))
    if has_vdf:
        vdf = steam / 'steamapps/libraryfolders.vdf'
        vdf.parent.mkdir(parents=True)
        vdf.write_text('"libraryfolders" { "0" { "path" "' + str(library).replace('\\', '\\\\') + '" } }', encoding='utf-8')
    exe = library / "steamapps/common/Sid Meier's Civilization VI/Base/Binaries/Win64Steam/CivilizationVI.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b'MZ')
    assert game_launcher._find_game_exe_win32() == str(exe)


def test_version_uses_installed_distribution_name(monkeypatch):
    calls = []
    monkeypatch.setattr(version.importlib.metadata, 'version', lambda name: calls.append(name) or '1.1.11')
    assert version._get_version() == '1.1.11'
    assert calls == ['civ6-mcp']


def test_windows_launch_uses_registered_steam_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'win32')
    steam = tmp_path / 'Steam on S'
    steam.mkdir()
    executable = steam / 'steam.exe'
    executable.write_bytes(b'MZ')
    monkeypatch.setattr(game_launcher, 'windows_steam_roots', lambda: [str(steam)])
    monkeypatch.setattr(game_launcher, '_dismiss_crash_dialogs_sync', lambda: [])
    monkeypatch.setattr(game_launcher, 'is_game_running', lambda: False)
    monkeypatch.setattr(game_launcher, '_wait_for_game_process', lambda timeout: 1)
    monkeypatch.setattr(game_launcher, '_wait_for_tuner_port', lambda: True)
    calls = []
    monkeypatch.setattr(game_launcher, '_launch_windows_application', calls.append)
    assert 'FireTuner port is open' in game_launcher._launch_game_sync()
    assert calls == [[str(executable), '-applaunch', '289070']]


def test_version_fallback_reads_utf8_metadata_under_windows_default_encoding(monkeypatch, tmp_path):
    def missing(name):
        raise version.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(version.importlib.metadata, 'version', missing)
    monkeypatch.setattr(version, '__file__', str(tmp_path / 'src/civ_mcp/version.py'))
    (tmp_path / 'pyproject.toml').write_text('description = "中文 — 游戏"\nversion = "1.2.3"\n', encoding='utf-8')
    assert version._get_version() == '1.2.3'


def test_windows_uri_and_direct_exe_fallback_use_desktop_owner(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(game_launcher, 'windows_steam_roots', lambda: [])
    monkeypatch.setattr(game_launcher, '_dismiss_crash_dialogs_sync', lambda: [])
    monkeypatch.setattr(game_launcher, 'is_game_running', lambda: False)
    waits = iter([None, 1])
    monkeypatch.setattr(game_launcher, '_wait_for_game_process', lambda timeout: next(waits))
    monkeypatch.setattr(game_launcher, '_wait_for_tuner_port', lambda: True)
    executable = r'D:\游戏 路径\CivilizationVI.exe'
    monkeypatch.setattr(game_launcher, '_find_game_exe_win32', lambda: executable)
    calls = []
    monkeypatch.setattr(game_launcher, '_launch_windows_application', calls.append)
    assert 'FireTuner port is open' in game_launcher._launch_game_sync()
    assert calls == [['steam://run/289070'], [executable]]


def test_knowledge_child_remains_utf8_with_parent_utf8_environment_disabled(monkeypatch, tmp_path):
    root = build(tmp_path)
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    result = json.loads(asyncio.run(knowledge.search('写作', topic='technologies', limit=1)))
    assert result['status'] == 'found', result
    assert result['results'][0]['names']['zh_Hans_CN'] == '写作'


def test_windows_save_parser_cli_does_not_require_sigpipe():
    script = Path(__file__).resolve().parents[1] / 'scripts/parse_save.py'
    result = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--help'],
                            capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout
