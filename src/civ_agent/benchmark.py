"""基准存档安装（审查 D1）。

读档入口 ``civ_mcp.game_lifecycle.load_save_from_frontend`` 会把存档名插值进 Lua，
因此只接受 ``^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$``。用户的基准存档名是
``吉尔伽美什_turn_1``（非 ASCII 开头），必然被拒。

本模块把它**复制**成 ASCII 名，而不是放宽校验：校验本身是防注入的，放宽需要额外
论证，而复制一份副本不触碰这条安全边界。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

#: 与 ``civ_mcp/game_lifecycle.py`` 的 ``_SAVE_NAME`` 保持一致。
ASCII_SAVE_NAME = "benchmark_start"

DEFAULT_SOURCE_NAME = "吉尔伽美什_turn_1"


class BenchmarkSaveError(RuntimeError):
    """基准存档缺失或不可安装；消息必须说明找过哪里。"""


@dataclass(frozen=True, slots=True)
class InstallResult:
    """安装结果；``installed=False`` 表示目标已存在且内容一致。"""

    path: Path
    installed: bool
    identical_to_source: bool


def save_dir() -> Path:
    """Civ6 普通存档目录（平台判定交给 game_launcher）。"""

    from civ_mcp.game_launcher import SINGLE_SAVE_DIR

    if not SINGLE_SAVE_DIR:
        raise BenchmarkSaveError("当前平台没有已知的 Civ6 存档目录。")
    return Path(SINGLE_SAVE_DIR)


def resolve_target(directory: Path, name: str = ASCII_SAVE_NAME) -> Path:
    """校验目标名合法，返回 ``.Civ6Save`` 路径。"""

    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name or ""):
        raise BenchmarkSaveError(
            f"存档名 {name!r} 不合法：读档入口只接受字母、数字、'.'、'_'、'-'，"
            "且必须以字母或数字开头。"
        )
    return Path(directory) / f"{name}.Civ6Save"


def install_benchmark_save(
    *,
    directory: Path | None = None,
    source_name: str = DEFAULT_SOURCE_NAME,
    target_name: str = ASCII_SAVE_NAME,
) -> InstallResult:
    """把基准存档安装为 ASCII 名的副本；已存在且一致时不重复写。

    源文件缺失或为空时**报错而不是猜**：一个空的基准存档会让整局从错误的起点开始。
    """

    base = save_dir() if directory is None else Path(directory)
    source = base / f"{source_name}.Civ6Save"
    target = resolve_target(base, target_name)

    if not source.is_file():
        raise BenchmarkSaveError(
            f"找不到基准存档：{source}\n"
            f"请确认文件名是 {source_name}.Civ6Save，或改用它所在目录。"
        )
    if source.stat().st_size <= 0:
        raise BenchmarkSaveError(f"基准存档为空文件，不能作为起点：{source}")

    if target.is_file() and target.stat().st_size > 0:
        # 已安装：只在内容一致时复用，否则报错而不是静默覆盖用户的存档。
        if _same_bytes(source, target):
            return InstallResult(path=target, installed=False, identical_to_source=True)
        raise BenchmarkSaveError(
            f"目标存档已存在但与基准存档不同：{target}\n"
            "它可能是上一次运行的产物；请先移走或改用一个新名字，"
            "本工具不会静默覆盖存档。"
        )

    base.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return InstallResult(path=target, installed=True, identical_to_source=True)


def _same_bytes(left: Path, right: Path) -> bool:
    return _digest(left) == _digest(right)


def _digest(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
