"""中文化 MCP 对外运行信息，而不改变机器契约。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


_TOOL_LABELS = {
    "get_game_overview": "读取游戏概览",
    "get_units": "查询单位",
    "get_cities": "查询城市",
    "get_barbarian_overview": "查询蛮族态势",
    "get_map_area": "查询地图区域",
    "get_combat_estimate": "评估战斗",
    "get_governance_brief": "读取治理简报",
    "get_belief_state": "读取信念状态",
    "get_belief_trace": "读取信念事件轨迹",
    "route_belief_decision": "路由信念决策",
    "submit_governance_proposal": "提交治理提案",
    "resolve_governance_council": "议会决议",
    "unit_action": "执行单位动作",
    "end_turn": "结束回合",
    "skip_remaining_units": "跳过其余单位",
}

_TEXT_REPLACEMENTS = (
    ("=== RUNTIME POLICY ===", "=== 运行策略 ==="),
    ("=== BELIEF ENGINE TURN BRIEF", "=== 信念引擎回合简报"),
    ("=== BELIEF CONTEXT ===", "=== 信念上下文 ==="),
    ("BELIEF_GATE_REQUIRED:", "治理门禁（BELIEF_GATE_REQUIRED）："),
    ("Error:", "错误："),
    ("Warning:", "警告："),
    ("CRITICAL:", "关键警告："),
    ("GAME OVER — DEFEAT.", "游戏结束——失败。"),
    ("GAME OVER — VICTORY!", "游戏结束——胜利！"),
    ("GAME OVER", "游戏结束"),
    ("Turn paused", "回合暂停"),
    ("Cannot end turn", "无法结束回合"),
    ("World Congress fires", "世界议会需要处理"),
    ("Belief Engine persistence is disabled in off mode.", "off 模式下，信念引擎持久化已禁用。"),
    ("Resolve the Belief Engine gate before retrying.", "请先处理信念引擎门禁，再重试。"),
    ("Use get_turn_brief before a key action; nearby hostiles require quantified combat evidence.", "关键行动前先调用 get_turn_brief；附近敌对单位必须有量化战斗证据。"),
    ("Turn ", "回合 "),
    ("Score:", "得分："),
    ("Gold:", "金币："),
    ("Science:", "科技："),
    ("Culture:", "文化："),
    ("Faith:", "信仰："),
    ("[Belief decision consumed:", "[已消耗信念决策："),
    ("default_route=", "默认路线="),
    ("blocking_scopes=", "阻塞范围="),
    ("flags=", "标记="),
    ("review=", "复核="),
    ("route=", "路线="),
)


def localize_model_result(tool_name: str, result: str) -> str:
    """为 MCP 调用方和模型提供中文语义层。

    原始 JSON 的字段、ID、枚举及游戏值保持不变，避免将展示语言混入治理、
    遥测或工具契约。对象结果添加 ``中文说明``；文本结果以中文摘要开头，
    再保留稳定的机器和游戏原始证据。
    此函数只应在原始结果已被记录和内部状态已更新之后调用。
    """

    if not result:
        return result
    parsed = _json_object(result)
    if parsed is not None:
        if "中文说明" in parsed:
            return result
        localized = deepcopy(parsed)
        _localize_json_semantic_values(localized)
        localized["中文说明"] = _json_summary(tool_name, parsed)
        return json.dumps(localized, ensure_ascii=False, separators=(",", ":"))
    return _text_presentation(tool_name, result)


def _json_object(result: str) -> dict[str, Any] | None:
    try:
        value = json.loads(result)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _json_summary(tool_name: str, payload: dict[str, Any]) -> dict[str, str]:
    status = "成功"
    if payload.get("disabled"):
        status = "已禁用"
    elif any(key in payload for key in ("error", "errors")):
        status = "失败"
    elif payload.get("authorized") is False or payload.get("blocked"):
        status = "受阻"
    return {
        "工具": _TOOL_LABELS.get(tool_name, f"执行工具 {tool_name}"),
        "状态": status,
        "说明": "以下原有字段为机器契约和游戏证据，字段名、ID、枚举及原始值保持不变。",
    }


def _localize_json_semantic_values(payload: dict[str, Any]) -> None:
    """只翻译约定为人类语义的顶层文字，不触碰游戏证据字段。"""

    for key in ("message", "reason", "warning"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = _localize_text(value)


def _text_presentation(tool_name: str, result: str) -> str:
    if result.startswith("【中文运行信息】"):
        return result
    status = "成功"
    if "Error:" in result or result.startswith("ERR"):
        status = "失败"
    elif "BELIEF_GATE_REQUIRED:" in result or "Cannot end turn" in result:
        status = "受阻"
    return "\n".join(
        (
            "【中文运行信息】",
            f"工具：{_TOOL_LABELS.get(tool_name, f'执行工具 {tool_name}')}（{tool_name}）",
            f"状态：{status}",
            "说明：下方保留机器标记、枚举或游戏原始文本，供精确调用与证据核验。",
            "",
            _localize_text(result),
        )
    )


def _localize_text(result: str) -> str:
    localized = result
    for source, target in _TEXT_REPLACEMENTS:
        localized = localized.replace(source, target)
    return re.sub(r"回合 (\d+)\s*->\s*(\d+)", r"回合 \1 → \2", localized)
