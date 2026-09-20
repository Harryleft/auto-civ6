"""Game Memory：一局一个时间戳 Markdown（v7 §8 / §9）。

文件由一段**元信息表**加若干 **Turn 小节**组成。元信息表在结尾用
``finish_game`` 回填 ``最终结果`` 与 ``结束回合``，不影响已追加的回合记录。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from civ_agent.observation import OurState, OpponentState, _text

MEMORY_DIR_ENV = "CIV6_AGENT_MEMORY_DIR"
DEFAULT_MEMORY_DIR = Path("memory") / "games"

UNKNOWN = "unknown"
_UNSET = ""
_GAME_ID = re.compile(r"^game_\d{8}_\d{6}(?:_\d+)?$")

_META_KEYS = (
    "开始时间",
    "基准存档",
    "文明",
    "领袖",
    "难度",
    "地图",
    "地图种子",
    "游戏种子",
    "速度",
    "规则集",
    "对手",
    "最终结果",
    "结束回合",
)


def memory_dir(base: Path | None = None) -> Path:
    """解析 Game Memory 目录；环境变量优先，其次仓库内 ``memory/games``。"""

    if base is not None:
        return Path(base)
    configured = os.environ.get(MEMORY_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_MEMORY_DIR


@dataclass(frozen=True, slots=True)
class GameStart:
    """一局游戏的固定起点事实。"""

    benchmark_save: str
    civilization: str = UNKNOWN
    leader: str = UNKNOWN
    difficulty: str = UNKNOWN
    map_name: str = UNKNOWN
    map_seed: str = UNKNOWN
    game_seed: str = UNKNOWN
    speed: str = UNKNOWN
    ruleset: str = UNKNOWN
    opponents: tuple[str, ...] = ()
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """一个回合的完整决策过程（v7 §9 的 Turn 小节）。"""

    turn: int
    our_state: OurState
    opponents: tuple[OpponentState, ...] = ()
    important_changes: tuple[str, ...] = ()
    memory_used: tuple[str, ...] = ()
    jev_assess: Iterable[str] = ()
    deepseek_summary: Iterable[str] = ()
    rule_queries: tuple[dict[str, Any], ...] = ()
    candidates: Iterable[str] = ()
    jev_review: Iterable[str] = ()
    final_action: Iterable[str] = ()
    execution: Iterable[str] = ()
    consequences: Iterable[str] = ()
    planning: str = ""

    def __post_init__(self) -> None:
        if self.turn < 1:
            raise ValueError("turn 必须从 1 开始。")


def _cell(value: Any) -> str:
    """表格单元格：转义分隔符，并把缺失值写成 unknown。"""

    text = _text(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _bullets(items: Iterable[str]) -> list[str]:
    collected = [str(item) for item in items]
    if not collected:
        return ["- （无）"]
    return [f"- {item}" for item in collected]


def _numbered(items: Iterable[str]) -> list[str]:
    collected = [str(item) for item in items]
    if not collected:
        return ["（无）"]
    return [f"{index}. {item}" for index, item in enumerate(collected, start=1)]


class GameMemoryWriter:
    """创建并追加一局游戏的 Markdown 经历。"""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = memory_dir(directory)

    # ---------- 生命周期 ----------

    def create_game(self, start: GameStart, *, now: datetime | None = None) -> Path:
        """新建一局的文件；同一秒重复调用会追加序号而不是覆盖。"""

        moment = now or datetime.now()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._allocate(moment)
        path.write_text(self._header(self._game_id(path), start, moment), encoding="utf-8")
        return path

    def append_turn(self, path: Path, record: TurnRecord) -> Path:
        """把一个回合追加到当前局文件末尾。"""

        self._require_existing(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + self._render_turn(record))
        return path

    def finish_game(self, path: Path, *, result: str, end_turn: int) -> Path:
        """回填最终结果；不改动已追加的回合记录。"""

        self._require_existing(path)
        if end_turn < 1:
            raise ValueError("end_turn 必须从 1 开始。")
        text = path.read_text(encoding="utf-8")
        text = self._set_field(text, "最终结果", _cell(result))
        text = self._set_field(text, "结束回合", str(end_turn))
        path.write_text(text, encoding="utf-8")
        return path

    # ---------- 内部 ----------

    def _allocate(self, moment: datetime) -> Path:
        stem = f"game_{moment:%Y%m%d_%H%M%S}"
        candidate = self.directory / f"{stem}.md"
        suffix = 1
        while candidate.exists() and suffix <= 999:
            candidate = self.directory / f"{stem}_{suffix}.md"
            suffix += 1
        if candidate.exists():
            raise FileExistsError(f"无法为 {stem} 分配唯一文件名。")
        return candidate

    @staticmethod
    def _game_id(path: Path) -> str:
        stem = path.stem
        if not _GAME_ID.fullmatch(stem):
            raise ValueError(f"非法的 Game Memory 文件名：{path.name}")
        return stem

    @staticmethod
    def _require_existing(path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Game Memory 文件不存在：{path}")

    @staticmethod
    def _set_field(text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"^(\|\s*\*\*{re.escape(key)}\*\*\s*\|)([^|]*)(\|)$", re.MULTILINE)
        replaced, count = pattern.subn(lambda match: f"{match.group(1)} {value} {match.group(3)}", text)
        if count != 1:
            raise ValueError(f"元信息表缺少唯一字段：{key}（匹配 {count} 次）")
        return replaced

    def _header(self, game_id: str, start: GameStart, moment: datetime) -> str:
        started = start.started_at or moment
        values = {
            "开始时间": f"{started:%Y-%m-%d %H:%M:%S}",
            "基准存档": start.benchmark_save,
            "文明": start.civilization,
            "领袖": start.leader,
            "难度": start.difficulty,
            "地图": start.map_name,
            "地图种子": start.map_seed,
            "游戏种子": start.game_seed,
            "速度": start.speed,
            "规则集": start.ruleset,
            "对手": "、".join(start.opponents) if start.opponents else UNKNOWN,
            "最终结果": UNKNOWN,
            "结束回合": _UNSET,
        }
        lines = [
            f"# Game {game_id}",
            "",
            "## 游戏基本信息",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
        ]
        lines.extend(f"| **{key}** | {_cell(values[key])} |" for key in _META_KEYS)
        return "\n".join(lines) + "\n"

    def _render_turn(self, record: TurnRecord) -> str:
        our = record.our_state
        lines = [
            "---",
            "",
            f"## Turn {record.turn}",
            "",
            "### 我方基本信息",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
            f"| 文明 | {_cell(our.civilization)} |",
            f"| 领袖 | {_cell(our.leader)} |",
            f"| 城市 | {_cell(our.cities)} |",
            f"| 总人口 | {_cell(our.total_population)} |",
            f"| 金币 | {_cell(our.gold)} |",
            f"| 每回合金币 | {_cell(our.gold_per_turn)} |",
            f"| 科技值 | {_cell(our.science)} |",
            f"| 文化值 | {_cell(our.culture)} |",
            f"| 信仰 | {_cell(our.faith)} |",
            f"| 军事力量 | {_cell(our.military_strength)} |",
            f"| 当前科技 | {_cell(our.current_research)} |",
            f"| 当前市政 | {_cell(our.current_civic)} |",
            f"| 当前政府 | {_cell(our.government)} |",
            f"| 战争状态 | {_cell('、'.join(our.at_war_with) if our.at_war_with else UNKNOWN)} |",
            f"| 胜利进度 | {_cell(our.score)} |",
            "",
            "### 对手基本信息",
            "",
        ]
        if record.opponents:
            for opponent in record.opponents:
                lines.extend(self._render_opponent(opponent))
        else:
            lines.extend(["> 未遇到任何其他文明，本回合不记录对手。", ""])

        lines.extend(
            [
                "### 本回合重要变化",
                "",
                *_bullets(record.important_changes),
                "",
                "### 历史经验",
                "",
                *_bullets(record.memory_used),
                "",
                "### Jev Assess",
                "",
                *_bullets(record.jev_assess),
                "",
                "### DeepSeek 决策摘要",
                "",
                *_bullets(record.deepseek_summary),
                "",
                "### Rule Search",
                "",
                *(self._render_rules(record.rule_queries)),
                "### 候选行动",
                "",
                *_numbered(record.candidates),
                "",
                "### Jev Review",
                "",
                *_bullets(record.jev_review),
                "",
                "### 最终行动",
                "",
                *_bullets(record.final_action),
                "",
                "### Runtime 执行结果",
                "",
                *_bullets(record.execution),
                "",
                "### 本回合后果",
                "",
                *_bullets(record.consequences),
                "",
                "### 长期目标",
                "",
                record.planning.strip() or "（未记录）",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _render_opponent(opponent: OpponentState) -> list[str]:
        return [
            f"#### {_cell(opponent.civilization)}",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
            f"| 领袖 | {_cell(opponent.leader)} |",
            f"| 已知城市数 | {_cell(opponent.known_cities)} |",
            f"| 科技值 | {_cell(opponent.science)} |",
            f"| 文化值 | {_cell(opponent.culture)} |",
            f"| 军事力量 | {_cell(opponent.military_strength)} |",
            f"| 外交关系 | {_cell(opponent.diplomatic_state)} |",
            f"| 战争状态 | {_cell(opponent.is_at_war)} |",
            f"| 胜利进度 | {_cell(opponent.victory_progress)} |",
            "",
        ]

    @staticmethod
    def _render_rules(queries: tuple[dict[str, Any], ...]) -> list[str]:
        if not queries:
            return ["无", ""]
        lines: list[str] = []
        for query in queries:
            lines.append(f"- 查询：{query.get('query', UNKNOWN)}")
            lines.append(f"  - 结果：{query.get('result', UNKNOWN)}")
        lines.append("")
        return lines
