"""按平台定位存档与 Steam，保留 macOS 原有默认路径。"""

import os
from pathlib import Path
import sys


def windows_registry_path(hive: str, key: str, value: str) -> str | None:
    """注册表仅提供路径候选，不存在或不可读时继续使用默认位置。"""
    try:
        import winreg

        with winreg.OpenKey(getattr(winreg, hive), key) as handle:
            path, _ = winreg.QueryValueEx(handle, value)
        return os.path.expandvars(path) if isinstance(path, str) and path else None
    except (ImportError, OSError):
        return None


def single_save_directory() -> str:
    override = os.environ.get('CIV6_SAVE_DIR')
    if override is not None:
        path = Path(override).expanduser()
        if not override.strip() or not path.is_absolute():
            raise ValueError('CIV6_SAVE_DIR must be an absolute Single save directory')
        return str(path)
    if sys.platform == 'darwin':
        return os.path.expanduser(
            "~/Library/Application Support/Sid Meier's Civilization VI/"
            "Sid Meier's Civilization VI/Saves/Single"
        )
    if sys.platform == 'win32':
        documents = windows_registry_path(
            'HKEY_CURRENT_USER',
            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
            'Personal',
        ) or os.path.expanduser('~/Documents')
        return os.path.join(os.path.normpath(documents), "My Games", "Sid Meier's Civilization VI", 'Saves', 'Single')
    return os.path.expanduser("~/.local/share/aspyr-media/Sid Meier's Civilization VI/Saves/Single")


def windows_steam_roots() -> list[str]:
    candidates = [
        windows_registry_path('HKEY_CURRENT_USER', r'Software\Valve\Steam', 'SteamPath'),
        windows_registry_path('HKEY_LOCAL_MACHINE', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
        windows_registry_path('HKEY_LOCAL_MACHINE', r'SOFTWARE\Valve\Steam', 'InstallPath'),
        os.path.expandvars(r'C:\Program Files (x86)\Steam'),
        os.path.expandvars(r'C:\Program Files\Steam'),
    ]
    return list(dict.fromkeys(path for path in candidates if path))
