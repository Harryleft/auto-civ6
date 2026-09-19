"""中文化 MCP 对外运行信息，而不改变机器契约。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any
from civ_mcp.result_json import json_object


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
    "get_village_overview": "查询部落村落",
    "get_era_progress": "查询纪元进度",
    "get_spies": "查询间谍",
    "get_city_production": "查询城市生产",
    "get_settle_advisor": "查询建城建议",
    "get_global_settle_advisor": "查询全域建城建议",
    "get_pathing_estimate": "评估行军路径",
    "get_builder_tasks": "查询建造者任务",
    "get_empire_resources": "查询帝国资源",
    "get_strategic_map": "查询战略地图",
    "get_diplomacy": "查询外交状态",
    "get_tech_civics": "查询科技与市政",
    "get_pending_trades": "查询待处理交易",
    "get_policies": "查询政策配置",
    "get_notifications": "查询游戏通知",
    "get_pending_diplomacy": "查询待处理外交",
    "get_trade_routes": "查询商路",
    "get_trade_destinations": "查询贸易目的地",
    "get_great_people": "查询伟人池",
    "get_great_people_overview": "查询伟人总览",
    "get_unit_promotions": "查询单位晋升",
    "get_victory_progress": "查询胜利进度",
    "get_governors": "查询总督状态",
    "get_city_states": "查询城邦状态",
    "get_pantheon_beliefs": "查询万神殿信条",
    "get_religion_beliefs": "查询创教信条",
    "get_dedications": "查询时代着力点",
    "get_trade_options": "查询可交易项",
    "get_diary": "读取游戏日记",
    "get_district_advisor": "区域选址建议",
    "get_wonder_advisor": "奇观选址建议",
    "get_gp_advisor": "伟人激活建议",
    "get_purchasable_tiles": "查询可购地块",
    "get_world_congress": "查询世界议会",
    "get_religion_spread": "查询宗教传播",
    "get_religion_overview": "查询宗教总览",
    "get_climate_overview": "查询气候报告",
}

# These are game-changing tools whose textual result is an action receipt,
# rather than a normal read/query.  Keep the list here deliberately explicit:
# governance tools such as ``route_belief_decision`` return JSON about the
# control plane and must not be mistaken for a game mutation.
_ACTION_TOOL_NAMES = frozenset(
    {
        "appoint_governor",
        "assign_governor",
        "promote_governor",
        "promote_unit",
        "send_envoy",
        "choose_pantheon",
        "found_religion",
        "upgrade_unit",
        "choose_dedication",
        "respond_to_trade",
        "propose_trade",
        "propose_peace",
        "set_policies",
        "respond_to_diplomacy",
        "send_diplomatic_action",
        "form_alliance",
        "city_action",
        "unit_action",
        "skip_remaining_units",
        "set_city_production",
        "purchase_item",
        "set_research",
        "purchase_tile",
        "change_government",
        "recruit_great_person",
        "patronize_great_person",
        "reject_great_person",
        "queue_wc_votes",
        "set_city_focus",
        "spy_action",
        "end_turn",
        "load_save",
        "load_game_save",
        "load_save_from_menu",
        "restart_and_load",
    }
)

_ACTION_RECEIPT_LABELS = {
    "succeeded": "已验证成功",
    "submitted": "已提交待验证",
    "failed": "失败",
    "blocked": "受阻",
    "unknown": "结果未知",
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


def localize_model_result(
    tool_name: str,
    result: str,
    *,
    params: dict[str, Any] | None = None,
) -> str:
    """为 MCP 调用方和模型提供中文语义层。

    原始 JSON 的字段、ID、枚举及游戏值保持不变，避免将展示语言混入治理、
    遥测或工具契约。对象结果添加 ``中文说明``；文本结果以中文摘要开头，
    再保留稳定的机器和游戏原始证据。
    此函数只应在原始结果已被记录和内部状态已更新之后调用。
    """

    if not result:
        return result
    parsed = json_object(result)
    if parsed is not None:
        if "中文说明" in parsed:
            return result
        localized = deepcopy(parsed)
        _localize_json_semantic_values(localized)
        localized["中文说明"] = _json_summary(tool_name, parsed, params=params)
        return json.dumps(localized, ensure_ascii=False, separators=(",", ":"))
    return _text_presentation(tool_name, result, params=params)


def _json_summary(
    tool_name: str,
    payload: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    status = "成功"
    receipt = action_receipt_status(tool_name, _json_semantic_text(payload), params=params)
    if receipt is not None:
        status = receipt[1]
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


def _text_presentation(
    tool_name: str,
    result: str,
    *,
    params: dict[str, Any] | None = None,
) -> str:
    if result.startswith("【中文运行信息】"):
        return result
    receipt = action_receipt_status(tool_name, result, params=params)
    if receipt is None:
        status = "成功"
        if "Error:" in result or result.startswith("ERR"):
            status = "失败"
        elif "BELIEF_GATE_REQUIRED:" in result or "Cannot end turn" in result:
            status = "受阻"
    else:
        status = receipt[1]
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


def action_receipt_status(
    tool_name: str,
    result: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Classify a game mutation without changing its raw marker.

    The first tuple item is a stable internal receipt status.  The second is
    its Chinese model-facing label.  ``submitted`` intentionally remains
    distinct from ``unknown`` for presentation, while the pipeline records it
    as the existing fail-closed ``unknown`` outcome until a read-back proves
    the postcondition.
    """

    if not _is_action_tool(tool_name, params):
        return None
    text = str(result or "")
    upper = text.upper()

    # The order is important: a mutation can report an unknown outcome using
    # an Error/ERR prefix, and a gate/blocker is not an ordinary failure.
    if any(
        marker in upper
        for marker in (
            "BELIEF_GATE_REQUIRED",
            "CANNOT END TURN",
            "END TURN BLOCKED",
            "TURN PAUSED",
            "ENDTURN_BLOCKING_",
            "GATE:RELOAD_UNCONFIRMED",
        )
    ):
        return "blocked", _ACTION_RECEIPT_LABELS["blocked"]
    if any(
        marker in upper
        for marker in (
            "OUTCOME_UNKNOWN",
            "RESULT UNKNOWN",
            "OUTCOME IS UNKNOWN",
            "CONNECTION LOST MID-COMMAND",
            "MAY HAVE ALREADY BEEN EXECUTED",
            "HANG:",
            "UNKNOWN:RELOAD_PENDING",
            "UNKNOWN:END_TURN",
        )
    ):
        return "unknown", _ACTION_RECEIPT_LABELS["unknown"]
    if (
        text.startswith(("Error:", "错误："))
        or "ERROR:" in upper
        or "错误：" in text
        or text.startswith("ERR")
        or any(
            marker in upper
            for marker in ("SILENT_FAILURE", "CANNOT_", "FAILED|", "FAILED ")
        )
    ):
        return "failed", _ACTION_RECEIPT_LABELS["failed"]

    # A turn transition and explicit read-back markers are proof, not merely
    # an acknowledgement from RequestOperation.
    if (
        tool_name == "end_turn"
        and re.search(r"\bTURN\s+\d+\s*(?:->|→)\s*\d+", upper)
    ) or any(
        marker in upper
        for marker in (
            "(VERIFIED",
            " VERIFIED",
            "READBACK_",
            "CONFIRMED",
            "MATCH",
            "(VERIFIED)",
        )
    ):
        return "succeeded", _ACTION_RECEIPT_LABELS["succeeded"]

    # Explicit requested/submitted markers are useful to callers, but they
    # are not a postcondition.  Unmarked successful mutation text is also
    # conservative by default: the game may have accepted an async request
    # while the state query is still stale.
    return "submitted", _ACTION_RECEIPT_LABELS["submitted"]


def _is_action_tool(tool_name: str, params: dict[str, Any] | None) -> bool:
    if tool_name == "run_lua":
        return str((params or {}).get("context", "gamecore")).lower() == "ingame"
    return tool_name in _ACTION_TOOL_NAMES


def _json_semantic_text(payload: dict[str, Any]) -> str:
    """Build only semantic text for receipt detection; keep JSON untouched."""

    values: list[str] = []
    for key in ("message", "reason", "warning", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def _localize_text(result: str) -> str:
    localized = result
    for source, target in _TEXT_REPLACEMENTS:
        localized = localized.replace(source, target)
    return re.sub(r"回合 (\d+)\s*->\s*(\d+)", r"回合 \1 → \2", localized)
