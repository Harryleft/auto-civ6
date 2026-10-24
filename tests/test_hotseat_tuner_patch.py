"""Patch refuses unsafe inputs/destinations before touching an executable."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def patcher():
    spec = importlib.util.spec_from_file_location("hotseat_patch_test", Path(__file__).parents[1] / "scripts/hotseat_tuner_patch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_build_never_enters_macho_parser(patcher, monkeypatch):
    def forbidden(_):
        pytest.fail("unknown binary was parsed")
    monkeypatch.setattr(patcher, "layout", forbidden)
    with pytest.raises(ValueError, match="Unknown/already patched"):
        patcher.patch_bytes(b"unsupported local executable")


@pytest.mark.parametrize("source,target", [(0, 1), (0, 1 << 27), (1 << 28, 0)])
def test_unencodable_branch_is_refused(patcher, source, target):
    with pytest.raises(ValueError):
        patcher.branch(source, target)


def test_hardlinked_launcher_is_refused_before_signing(patcher, tmp_path):
    source, destination = tmp_path / "stock.app", tmp_path / "test.app"
    for root in (source, destination):
        (root / "Contents/MacOS").mkdir(parents=True)
        (root / "Contents/Info.plist").write_text("same metadata")
    launcher = source / "Contents/MacOS/Civ6_Exe"
    launcher.write_bytes(b"stock launcher")
    (destination / "Contents/MacOS/Civ6_Exe").hardlink_to(launcher)
    with pytest.raises(ValueError, match="shares a writable/signable inode"):
        patcher.ensure_independent_files(source, destination)
    assert launcher.read_bytes() == b"stock launcher"
