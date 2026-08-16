"""双轨工具输出契约：结构化事实视图 + 叙述视图。

每个高频查询工具返回同一个信封（envelope）：

.. code-block:: json

    {
      "v": 1,
      "tool": "get_units",
      "turn": 56,
      "source": "civ_mcp:GameState",
      "coverage": {"own_units": "COMPLETE", "foreign_units": "CURRENTLY_VISIBLE"},
      "facts": {"own_units": [{"unit_id": 131073, "x": 32, ...}]},
      "narrated": "10 units:\\n  侦察兵 (UNIT_SCOUT) at (37,32) ..."
    }

模型优先消费 ``facts``（字段级 schema，无歧义、无需从自由文本解析）；
``narrated`` 保留原有叙述文本，供日志、观测正常化器与人工阅读回退。
``coverage`` 使用图工程（graph_plan）同款三值覆盖语义：

- ``COMPLETE`` — 当前全集事实，缺项即不存在；
- ``CURRENTLY_VISIBLE`` — 仅当前视野内可见，缺项不代表不存在；
- ``KNOWN_HISTORY`` — 已揭示历史事实，未观察 ≠ 已删除。

本模块只负责"DTO → 信封"的纯序列化，不访问游戏或 MCP 上下文。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from civ_mcp import lua as lq

ENVELOPE_VERSION = 1
SOURCE = "civ_mcp:GameState"

# 图工程同款覆盖语义常量（与 civ6_belief_engine.graph.model.Coverage 对齐）
COVERAGE_COMPLETE = "COMPLETE"
COVERAGE_CURRENTLY_VISIBLE = "CURRENTLY_VISIBLE"
COVERAGE_KNOWN_HISTORY = "KNOWN_HISTORY"


def dumps(payload: dict[str, Any]) -> str:
    """序列化信封为模型面对的结果字符串。"""
    return json.dumps(payload, ensure_ascii=False)


def parse_envelope(result: str) -> dict[str, Any] | None:
    """若结果为双轨信封则返回其 dict，否则返回 None。"""
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != ENVELOPE_VERSION:
        return None
    if not isinstance(payload.get("facts"), dict):
        return None
    if not isinstance(payload.get("narrated"), str):
        return None
    return payload


def _envelope(
    tool: str,
    turn: int | None,
    facts: dict[str, Any],
    coverage: dict[str, str],
    narrated: str,
) -> dict[str, Any]:
    return {
        "v": ENVELOPE_VERSION,
        "tool": tool,
        "turn": turn if isinstance(turn, int) else "?",
        "source": SOURCE,
        "coverage": coverage,
        "facts": facts,
        "narrated": narrated,
    }


def units_envelope(
    *,
    turn: int | None,
    units: list[lq.UnitInfo],
    threats: list[lq.ThreatInfo] | None,
    trade_status: lq.TradeRouteStatus | None,
    narrated: str,
) -> dict[str, Any]:
    """get_units 双轨信封。

    ``own_units`` 为当前存在的己方单位全集（已消耗单位不在引擎状态中）；
    ``foreign_units`` 只含当前视野内的敌方/中立军事单位。
    """
    facts: dict[str, Any] = {"own_units": [asdict(u) for u in units]}
    if threats:
        facts["foreign_units"] = [asdict(t) for t in threats]
    if trade_status is not None:
        facts["trade_routes"] = {
            "capacity": trade_status.capacity,
            "active_count": trade_status.active_count,
            "traders": [asdict(t) for t in trade_status.traders],
            "ghost_count": trade_status.ghost_count,
        }
    return _envelope(
        "get_units",
        turn,
        facts,
        {
            "own_units": COVERAGE_COMPLETE,
            "foreign_units": COVERAGE_CURRENTLY_VISIBLE,
            "trade_routes": COVERAGE_COMPLETE,
        },
        narrated,
    )


def cities_envelope(
    *,
    turn: int | None,
    cities: list[lq.CityInfo],
    distances: list[str] | None,
    narrated: str,
) -> dict[str, Any]:
    """get_cities 双轨信封。``cities`` 为己方城市全集。"""
    facts: dict[str, Any] = {"cities": [asdict(c) for c in cities]}
    if distances:
        facts["city_distances"] = list(distances)
    return _envelope(
        "get_cities",
        turn,
        facts,
        {"cities": COVERAGE_COMPLETE},
        narrated,
    )


def map_area_envelope(
    *,
    turn: int | None,
    center_x: int,
    center_y: int,
    radius: int,
    tiles: list[lq.TileInfo],
    narrated: str,
) -> dict[str, Any]:
    """get_map_area 双轨信封。

    请求区域本身是全集（``tiles`` = COMPLETE）；每个地块的内容覆盖由
    各自的 ``visibility`` 字段标注（visible / revealed / unexplored）。
    """
    facts: dict[str, Any] = {
        "center": [center_x, center_y],
        "radius": radius,
        "tiles": [asdict(t) for t in tiles],
    }
    return _envelope(
        "get_map_area",
        turn,
        facts,
        {"tiles": COVERAGE_COMPLETE},
        narrated,
    )


def barbarian_envelope(
    *,
    turn: int | None,
    overview: lq.BarbarianOverview,
    narrated: str,
) -> dict[str, Any]:
    """get_barbarian_overview 双轨信封。

    ``camps`` 以已揭示地块为限（KNOWN_HISTORY：离开视野仍列出）；
    ``units`` 只含当前可见单位（CURRENTLY_VISIBLE）。
    """
    facts: dict[str, Any] = {
        "camps": [asdict(c) for c in overview.camps],
        "units": [asdict(u) for u in overview.units],
    }
    return _envelope(
        "get_barbarian_overview",
        turn,
        facts,
        {
            "camps": COVERAGE_KNOWN_HISTORY,
            "units": COVERAGE_CURRENTLY_VISIBLE,
        },
        narrated,
    )


def combat_estimate_envelope(
    *,
    turn: int | None,
    estimate: lq.CombatEstimate | None,
    narrated: str,
) -> dict[str, Any]:
    """get_combat_estimate 双轨信封。

    ``available=false`` 表示游戏无法为这组单位给出量化评估（如目标不可攻击）；
    ``estimate`` 为该特定对阵的权威预览，覆盖语义为 COMPLETE。
    """
    facts: dict[str, Any] = {"available": estimate is not None}
    if estimate is not None:
        facts["estimate"] = asdict(estimate)
    return _envelope(
        "get_combat_estimate",
        turn,
        facts,
        {"estimate": COVERAGE_COMPLETE},
        narrated,
    )


def diplomacy_envelope(
    *,
    turn: int | None,
    civs: list[lq.CivInfo],
    narrated: str,
) -> dict[str, Any]:
    """get_diplomacy 双轨信封。

    ``civs`` 为所有已知文明（含未见面者，以 ``has_met`` 标注）；每方可见
    城市等情报字段只反映当前可见度。
    """
    facts: dict[str, Any] = {"civs": [asdict(c) for c in civs]}
    return _envelope(
        "get_diplomacy",
        turn,
        facts,
        {"civs": COVERAGE_COMPLETE},
        narrated,
    )


def tech_civics_envelope(
    *,
    turn: int | None,
    status: lq.TechCivicStatus,
    narrated: str,
) -> dict[str, Any]:
    """get_tech_civics 双轨信封。己方科技/市政状态为全集事实。"""
    return _envelope(
        "get_tech_civics",
        turn,
        asdict(status),
        {
            "research": COVERAGE_COMPLETE,
            "civics": COVERAGE_COMPLETE,
        },
        narrated,
    )


def victory_progress_envelope(
    *,
    turn: int | None,
    progress: lq.VictoryProgress,
    narrated: str,
) -> dict[str, Any]:
    """get_victory_progress 双轨信封。

    ``players`` 覆盖全部已知文明（未见面者字段为默认占位）；敌方情报字段
    只反映当前可见度。
    """
    return _envelope(
        "get_victory_progress",
        turn,
        asdict(progress),
        {
            "players": COVERAGE_COMPLETE,
            "demographics": COVERAGE_COMPLETE,
        },
        narrated,
    )


def great_people_overview_envelope(
    *,
    turn: int | None,
    overview: lq.GreatPeopleOverview,
    narrated: str,
) -> dict[str, Any]:
    """get_great_people_overview 双轨信封。

    ``standings``/``timeline`` 为当前全集；``history`` 为已发生事实
    （KNOWN_HISTORY）。
    """
    return _envelope(
        "get_great_people_overview",
        turn,
        asdict(overview),
        {
            "standings": COVERAGE_COMPLETE,
            "timeline": COVERAGE_COMPLETE,
            "history": COVERAGE_KNOWN_HISTORY,
            "own_units": COVERAGE_COMPLETE,
        },
        narrated,
    )


def city_production_envelope(
    *,
    turn: int | None,
    city_id: int,
    options: list[lq.ProductionOption],
    narrated: str,
) -> dict[str, Any]:
    """get_city_production 双轨信封。``options`` 为该城市当前可生产全集。"""
    facts: dict[str, Any] = {
        "city_id": city_id,
        "options": [asdict(o) for o in options],
    }
    return _envelope(
        "get_city_production",
        turn,
        facts,
        {"options": COVERAGE_COMPLETE},
        narrated,
    )


def settle_envelope(
    *,
    turn: int | None,
    tool: str,
    unit_id: int,
    candidates: list[lq.SettleCandidate],
    source: str,
    narrated: str,
) -> dict[str, Any]:
    """get_settle_advisor / get_global_settle_advisor 双轨信封。

    ``source`` 为候选来源：``local``（定居者周边 5 格）/ ``global``
    （已揭示地图回退扫描）/ ``none``（无候选）。候选基于已揭示地块，
    覆盖语义为 KNOWN_HISTORY。
    """
    facts: dict[str, Any] = {
        "unit_id": unit_id,
        "source": source,
        "candidates": [asdict(c) for c in candidates],
    }
    return _envelope(
        tool,
        turn,
        facts,
        {"candidates": COVERAGE_KNOWN_HISTORY},
        narrated,
    )


def era_progress_envelope(
    *,
    turn: int | None,
    status: lq.EraProgress,
    narrated: str,
) -> dict[str, Any]:
    """get_era_progress 双轨信封。纪元序列与各文明当前纪元为全集事实。"""
    return _envelope(
        "get_era_progress",
        turn,
        asdict(status),
        {
            "eras": COVERAGE_COMPLETE,
            "players": COVERAGE_COMPLETE,
            "local_age": COVERAGE_COMPLETE,
        },
        narrated,
    )


def trade_routes_envelope(
    *,
    turn: int | None,
    status: lq.TradeRouteStatus,
    narrated: str,
) -> dict[str, Any]:
    """get_trade_routes 双轨信封。商路容量与商人状态为全集事实。"""
    return _envelope(
        "get_trade_routes",
        turn,
        asdict(status),
        {
            "routes": COVERAGE_COMPLETE,
            "traders": COVERAGE_COMPLETE,
        },
        narrated,
    )


def pathing_envelope(
    *,
    turn: int | None,
    unit_id: int,
    target_x: int,
    target_y: int,
    estimate: lq.PathingEstimate,
    narrated: str,
) -> dict[str, Any]:
    """get_pathing_estimate 双轨信封。针对给定单位与目的地的权威评估。"""
    facts: dict[str, Any] = {
        "unit_id": unit_id,
        "target_x": target_x,
        "target_y": target_y,
        "estimate": asdict(estimate),
    }
    return _envelope(
        "get_pathing_estimate",
        turn,
        facts,
        {"estimate": COVERAGE_COMPLETE},
        narrated,
    )
