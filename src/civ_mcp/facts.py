"""单轨工具输出契约：结构化事实视图。

每个高频查询工具返回同一个信封（envelope）：

.. code-block:: json

    {
      "v": 1,
      "tool": "get_units",
      "turn": 56,
      "source": "civ_mcp:GameState",
      "coverage": {"own_units": "COMPLETE", "foreign_units": "CURRENTLY_VISIBLE"},
      "facts": {"own_units": [{"unit_id": 131073, "x": 32, ...}]}
    }

模型与观测正常化器都只消费 ``facts``（字段级 schema，无歧义、无需从
自由文本解析）。``coverage`` 使用图工程（graph_plan）同款三值覆盖语义：

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
    """若结果为单轨信封则返回其 dict，否则返回 None。"""
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
    return payload


def _envelope(
    tool: str,
    turn: int | None,
    facts: dict[str, Any],
    coverage: dict[str, str],
    *,
    warning: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": ENVELOPE_VERSION,
        "tool": tool,
        "turn": turn if isinstance(turn, int) else "?",
        "source": SOURCE,
        "coverage": coverage,
        "facts": facts,
    }
    if warning:
        payload["warning"] = warning
    return payload


def units_envelope(
    *,
    turn: int | None,
    units: list[lq.UnitInfo],
    threats: list[lq.ThreatInfo] | None,
    trade_status: lq.TradeRouteStatus | None,
) -> dict[str, Any]:
    """get_units 单轨信封。

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
    )


def cities_envelope(
    *,
    turn: int | None,
    cities: list[lq.CityInfo],
    distances: list[str] | None,
) -> dict[str, Any]:
    """get_cities 单轨信封。``cities`` 为己方城市全集。"""
    facts: dict[str, Any] = {"cities": [asdict(c) for c in cities]}
    if distances:
        facts["city_distances"] = list(distances)
    return _envelope(
        "get_cities",
        turn,
        facts,
        {"cities": COVERAGE_COMPLETE},
    )


def map_area_envelope(
    *,
    turn: int | None,
    center_x: int,
    center_y: int,
    radius: int,
    tiles: list[lq.TileInfo],
) -> dict[str, Any]:
    """get_map_area 单轨信封。

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
    )


def barbarian_envelope(
    *,
    turn: int | None,
    overview: lq.BarbarianOverview,
) -> dict[str, Any]:
    """get_barbarian_overview 单轨信封。

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
    )


def combat_estimate_envelope(
    *,
    turn: int | None,
    estimate: lq.CombatEstimate | None,
) -> dict[str, Any]:
    """get_combat_estimate 单轨信封。

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
    )


def diplomacy_envelope(
    *,
    turn: int | None,
    civs: list[lq.CivInfo],
) -> dict[str, Any]:
    """get_diplomacy 单轨信封。

    ``civs`` 为所有已知文明（含未见面者，以 ``has_met`` 标注）；每方可见
    城市等情报字段只反映当前可见度。``our_military`` 来自采集端附加的
    己方军力（原叙述文本 "vs our N" 的唯一事实来源）。
    """
    facts: dict[str, Any] = {"civs": [asdict(c) for c in civs]}
    our_military = next(
        (
            int(getattr(c, "_our_military"))
            for c in civs
            if isinstance(getattr(c, "_our_military", None), int)
        ),
        None,
    )
    if our_military is not None:
        facts["our_military"] = our_military
    return _envelope(
        "get_diplomacy",
        turn,
        facts,
        {"civs": COVERAGE_COMPLETE},
    )


def tech_civics_envelope(
    *,
    turn: int | None,
    status: lq.TechCivicStatus,
) -> dict[str, Any]:
    """get_tech_civics 单轨信封。己方科技/市政状态为全集事实。"""
    return _envelope(
        "get_tech_civics",
        turn,
        asdict(status),
        {
            "research": COVERAGE_COMPLETE,
            "civics": COVERAGE_COMPLETE,
        },
    )


def victory_progress_envelope(
    *,
    turn: int | None,
    progress: lq.VictoryProgress,
) -> dict[str, Any]:
    """get_victory_progress 单轨信封。

    ``players`` 覆盖全部已知文明（未见面者字段为默认占位）；敌方情报字段
    只反映当前可见度。``enabled_victories`` 原为 set，序列化前规范为列表。
    """
    facts = asdict(progress)
    enabled = facts.get("enabled_victories")
    if isinstance(enabled, set):
        facts["enabled_victories"] = sorted(str(item) for item in enabled)
    return _envelope(
        "get_victory_progress",
        turn,
        facts,
        {
            "players": COVERAGE_COMPLETE,
            "demographics": COVERAGE_COMPLETE,
        },
    )


def great_people_overview_envelope(
    *,
    turn: int | None,
    overview: lq.GreatPeopleOverview,
) -> dict[str, Any]:
    """get_great_people_overview 单轨信封。

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
    )


def city_production_envelope(
    *,
    turn: int | None,
    city_id: int,
    options: list[lq.ProductionOption],
) -> dict[str, Any]:
    """get_city_production 单轨信封。``options`` 为该城市当前可生产全集。"""
    facts: dict[str, Any] = {
        "city_id": city_id,
        "options": [asdict(o) for o in options],
    }
    return _envelope(
        "get_city_production",
        turn,
        facts,
        {"options": COVERAGE_COMPLETE},
    )


def settle_envelope(
    *,
    turn: int | None,
    tool: str,
    unit_id: int,
    candidates: list[lq.SettleCandidate],
    source: str,
) -> dict[str, Any]:
    """get_settle_advisor / get_global_settle_advisor 单轨信封。

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
    )


def era_progress_envelope(
    *,
    turn: int | None,
    status: lq.EraProgress,
) -> dict[str, Any]:
    """get_era_progress 单轨信封。纪元序列与各文明当前纪元为全集事实。"""
    return _envelope(
        "get_era_progress",
        turn,
        asdict(status),
        {
            "eras": COVERAGE_COMPLETE,
            "players": COVERAGE_COMPLETE,
            "local_age": COVERAGE_COMPLETE,
        },
    )


def trade_routes_envelope(
    *,
    turn: int | None,
    status: lq.TradeRouteStatus,
) -> dict[str, Any]:
    """get_trade_routes 单轨信封。商路容量与商人状态为全集事实。"""
    return _envelope(
        "get_trade_routes",
        turn,
        asdict(status),
        {
            "routes": COVERAGE_COMPLETE,
            "traders": COVERAGE_COMPLETE,
        },
    )


def pathing_envelope(
    *,
    turn: int | None,
    unit_id: int,
    target_x: int,
    target_y: int,
    estimate: lq.PathingEstimate,
) -> dict[str, Any]:
    """get_pathing_estimate 单轨信封。针对给定单位与目的地的权威评估。"""
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
    )


def village_envelope(
    *,
    turn: int | None,
    overview: lq.VillageOverview,
) -> dict[str, Any]:
    """get_village_overview 单轨信封。

    只反映当前仍存在的村落：任一单位踏入即消失。已揭示地块上的村落从
    结果中消失只说明已被取用，覆盖语义为 KNOWN_HISTORY。
    """
    facts: dict[str, Any] = {"huts": [asdict(h) for h in overview.huts]}
    return _envelope(
        "get_village_overview",
        turn,
        facts,
        {"huts": COVERAGE_KNOWN_HISTORY},
    )


def spies_envelope(
    *,
    turn: int | None,
    spies: list[lq.SpyInfo],
) -> dict[str, Any]:
    """get_spies 单轨信封。己方间谍为当前全集（COMPLETE）。"""
    facts: dict[str, Any] = {"spies": [asdict(s) for s in spies]}
    return _envelope(
        "get_spies",
        turn,
        facts,
        {"spies": COVERAGE_COMPLETE},
    )


def builder_tasks_envelope(
    *,
    turn: int | None,
    tasks: list[lq.BuilderTask],
    builders: list[lq.BuilderInfo],
) -> dict[str, Any]:
    """get_builder_tasks 单轨信封。

    任务板来自己方领土（当前可见）的改进需求扫描，任务与建造者为全集。
    """
    facts: dict[str, Any] = {
        "tasks": [asdict(t) for t in tasks],
        "builders": [asdict(b) for b in builders],
    }
    return _envelope(
        "get_builder_tasks",
        turn,
        facts,
        {
            "tasks": COVERAGE_COMPLETE,
            "builders": COVERAGE_COMPLETE,
        },
    )


def empire_resources_envelope(
    *,
    turn: int | None,
    stockpiles: list[lq.ResourceStockpile],
    owned: list[lq.OwnedResource],
    nearby: list[lq.NearbyResource],
    luxuries: dict[str, int],
) -> dict[str, Any]:
    """get_empire_resources 单轨信封。

    ``stockpiles``/``owned`` 为己方全集（COMPLETE）；``nearby`` 只含已揭示
    地块上的未认领资源（KNOWN_HISTORY，未观察 ≠ 不存在）。
    """
    facts: dict[str, Any] = {
        "stockpiles": [asdict(s) for s in stockpiles],
        "owned": [asdict(o) for o in owned],
        "nearby": [asdict(n) for n in nearby],
        "luxuries": dict(luxuries),
    }
    return _envelope(
        "get_empire_resources",
        turn,
        facts,
        {
            "stockpiles": COVERAGE_COMPLETE,
            "owned": COVERAGE_COMPLETE,
            "nearby": COVERAGE_KNOWN_HISTORY,
        },
    )


def notifications_envelope(
    *,
    turn: int | None,
    notifications: list[lq.GameNotification],
) -> dict[str, Any]:
    """get_notifications 单轨信封。当前活动通知为全集（COMPLETE）。"""
    facts: dict[str, Any] = {
        "notifications": [asdict(n) for n in notifications]
    }
    return _envelope(
        "get_notifications",
        turn,
        facts,
        {"notifications": COVERAGE_COMPLETE},
    )


def policies_envelope(
    *,
    turn: int | None,
    status: lq.GovernmentStatus,
) -> dict[str, Any]:
    """get_policies 单轨信封。政府与政策配置为全集事实。"""
    return _envelope(
        "get_policies",
        turn,
        asdict(status),
        {
            "government": COVERAGE_COMPLETE,
            "policies": COVERAGE_COMPLETE,
        },
    )


def strategic_map_envelope(
    *,
    turn: int | None,
    data: lq.StrategicMapData,
) -> dict[str, Any]:
    """get_strategic_map 单轨信封。

    ``fog_boundaries`` 来自己方城市（COMPLETE）；``unclaimed_resources``
    以已揭示地块为限（KNOWN_HISTORY）。
    """
    return _envelope(
        "get_strategic_map",
        turn,
        asdict(data),
        {
            "fog_boundaries": COVERAGE_COMPLETE,
            "unclaimed_resources": COVERAGE_KNOWN_HISTORY,
        },
    )


def pending_trades_envelope(
    *,
    turn: int | None,
    deals: list[lq.PendingDeal],
) -> dict[str, Any]:
    """get_pending_trades 单轨信封。当前待处理交易为全集（COMPLETE）。"""
    facts: dict[str, Any] = {"deals": [asdict(d) for d in deals]}
    return _envelope(
        "get_pending_trades",
        turn,
        facts,
        {"deals": COVERAGE_COMPLETE},
    )


def pending_diplomacy_envelope(
    *,
    turn: int | None,
    sessions: list[lq.DiplomacySession],
) -> dict[str, Any]:
    """get_pending_diplomacy 单轨信封。当前外交会话为全集（COMPLETE）。"""
    facts: dict[str, Any] = {"sessions": [asdict(s) for s in sessions]}
    return _envelope(
        "get_pending_diplomacy",
        turn,
        facts,
        {"sessions": COVERAGE_COMPLETE},
    )


def trade_destinations_envelope(
    *,
    turn: int | None,
    unit_id: int,
    destinations: list[lq.TradeDestination],
) -> dict[str, Any]:
    """get_trade_destinations 单轨信封。给定商人的可选目的地全集。"""
    facts: dict[str, Any] = {
        "unit_id": unit_id,
        "destinations": [asdict(d) for d in destinations],
    }
    return _envelope(
        "get_trade_destinations",
        turn,
        facts,
        {"destinations": COVERAGE_COMPLETE},
    )


def great_people_envelope(
    *,
    turn: int | None,
    people: list[lq.GreatPersonInfo],
) -> dict[str, Any]:
    """get_great_people 单轨信封。当前招募池为全集（COMPLETE）。"""
    facts: dict[str, Any] = {"people": [asdict(p) for p in people]}
    return _envelope(
        "get_great_people",
        turn,
        facts,
        {"people": COVERAGE_COMPLETE},
    )


def unit_promotions_envelope(
    *,
    turn: int | None,
    status: lq.UnitPromotionStatus,
) -> dict[str, Any]:
    """get_unit_promotions 单轨信封。该单位可用晋升为全集事实。"""
    return _envelope(
        "get_unit_promotions",
        turn,
        asdict(status),
        {"promotions": COVERAGE_COMPLETE},
    )


def governors_envelope(
    *,
    turn: int | None,
    status: lq.GovernorStatus,
) -> dict[str, Any]:
    """get_governors 单轨信封。总督点数与任免状态为全集事实。"""
    return _envelope(
        "get_governors",
        turn,
        asdict(status),
        {"governors": COVERAGE_COMPLETE},
    )


def city_states_envelope(
    *,
    turn: int | None,
    status: lq.EnvoyStatus,
) -> dict[str, Any]:
    """get_city_states 单轨信封。

    使者令牌为己方全集；城邦的 ``competition_complete`` 字段标注竞争
    信息是否完整（未见面文明仍可能竞争宗主地位）。
    """
    return _envelope(
        "get_city_states",
        turn,
        asdict(status),
        {
            "envoy_tokens": COVERAGE_COMPLETE,
            "city_states": COVERAGE_COMPLETE,
        },
    )


def pantheon_envelope(
    *,
    turn: int | None,
    status: lq.PantheonStatus,
) -> dict[str, Any]:
    """get_pantheon_beliefs 单轨信封。万神殿状态与可选信条为全集。"""
    return _envelope(
        "get_pantheon_beliefs",
        turn,
        asdict(status),
        {
            "pantheon": COVERAGE_COMPLETE,
            "beliefs": COVERAGE_COMPLETE,
        },
    )


def religion_founding_envelope(
    *,
    turn: int | None,
    status: lq.ReligionFoundingStatus,
) -> dict[str, Any]:
    """get_religion_beliefs 单轨信封。创教状态与可用信条为全集。"""
    return _envelope(
        "get_religion_beliefs",
        turn,
        asdict(status),
        {
            "religion": COVERAGE_COMPLETE,
            "beliefs": COVERAGE_COMPLETE,
        },
    )


def dedications_envelope(
    *,
    turn: int | None,
    status: lq.DedicationStatus,
) -> dict[str, Any]:
    """get_dedications 单轨信封。时代着力点状态与候选项为全集。"""
    return _envelope(
        "get_dedications",
        turn,
        asdict(status),
        {"dedications": COVERAGE_COMPLETE},
    )


def trade_options_envelope(
    *,
    turn: int | None,
    other_player_id: int,
    options: lq.DealOptions,
) -> dict[str, Any]:
    """get_trade_options 单轨信封。双方可交易项为全集（COMPLETE）。"""
    facts: dict[str, Any] = {
        "other_player_id": other_player_id,
        "options": asdict(options),
    }
    return _envelope(
        "get_trade_options",
        turn,
        facts,
        {"deal_options": COVERAGE_COMPLETE},
    )


def diary_envelope(
    *,
    turn: int | None,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """get_diary 单轨信封。

    日记是本地事件日志（非游戏世界事实），``entries`` 保留原始条目
    字段；覆盖语义 COMPLETE 表示按查询条件完整读取。
    """
    facts: dict[str, Any] = {
        "entries": [dict(e) for e in entries],
    }
    return _envelope(
        "get_diary",
        turn,
        facts,
        {"entries": COVERAGE_COMPLETE},
    )


def district_advisor_envelope(
    *,
    turn: int | None,
    city_id: int,
    district_type: str,
    placements: list[lq.DistrictPlacement],
    warning: str | None = None,
) -> dict[str, Any]:
    """get_district_advisor 单轨信封。

    有效地块按相邻加成排序（COMPLETE）；调用预算警告放入顶层
    ``warning`` 而非文本前缀。
    """
    facts: dict[str, Any] = {
        "city_id": city_id,
        "district_type": district_type,
        "placements": [asdict(p) for p in placements],
    }
    return _envelope(
        "get_district_advisor",
        turn,
        facts,
        {"placements": COVERAGE_COMPLETE},
        warning=warning,
    )


def wonder_advisor_envelope(
    *,
    turn: int | None,
    city_id: int,
    wonder_name: str,
    placements: list[lq.WonderPlacement],
    warning: str | None = None,
) -> dict[str, Any]:
    """get_wonder_advisor 单轨信封。

    有效地块按位移成本排序（COMPLETE）；调用预算警告放入顶层
    ``warning`` 而非文本前缀。
    """
    facts: dict[str, Any] = {
        "city_id": city_id,
        "wonder_name": wonder_name,
        "placements": [asdict(p) for p in placements],
    }
    return _envelope(
        "get_wonder_advisor",
        turn,
        facts,
        {"placements": COVERAGE_COMPLETE},
        warning=warning,
    )


def purchasable_tiles_envelope(
    *,
    turn: int | None,
    city_id: int,
    tiles: list[lq.PurchasableTile],
) -> dict[str, Any]:
    """get_purchasable_tiles 单轨信封。该城市可购地块为全集（COMPLETE）。"""
    facts: dict[str, Any] = {
        "city_id": city_id,
        "tiles": [asdict(t) for t in tiles],
    }
    return _envelope(
        "get_purchasable_tiles",
        turn,
        facts,
        {"tiles": COVERAGE_COMPLETE},
    )


def gp_advisor_envelope(
    *,
    turn: int | None,
    unit_index: int,
    result: lq.GPAdvisorResult | None,
) -> dict[str, Any]:
    """get_gp_advisor 单轨信封。

    ``available=false`` 表示该单位不是伟人单位；``result`` 为激活候选
    城市全集（COMPLETE）。
    """
    facts: dict[str, Any] = {
        "unit": unit_index,
        "available": result is not None,
    }
    if result is not None:
        facts["result"] = asdict(result)
    return _envelope(
        "get_gp_advisor",
        turn,
        facts,
        {"cities": COVERAGE_COMPLETE},
    )


def world_congress_envelope(
    *,
    turn: int | None,
    status: lq.WorldCongressStatus,
) -> dict[str, Any]:
    """get_world_congress 单轨信封。议会状态、决议与提案为全集。"""
    return _envelope(
        "get_world_congress",
        turn,
        asdict(status),
        {"congress": COVERAGE_COMPLETE},
    )


def religion_spread_envelope(
    *,
    turn: int | None,
    status: lq.ReligionStatus,
) -> dict[str, Any]:
    """get_religion_spread 单轨信封。

    ``cities`` 只含当前可见城市（CURRENTLY_VISIBLE）；``summary`` 为
    已知聚合（COMPLETE）。
    """
    return _envelope(
        "get_religion_spread",
        turn,
        asdict(status),
        {
            "cities": COVERAGE_CURRENTLY_VISIBLE,
            "summary": COVERAGE_COMPLETE,
        },
    )


def religion_overview_envelope(
    *,
    turn: int | None,
    status: lq.ReligionOverview,
) -> dict[str, Any]:
    """get_religion_overview 单轨信封。

    ``religions`` 为已创建宗教全集；``players`` 只含已见面主要文明
    （未见面创始者遮蔽为 Unmet，KNOWN_HISTORY）。
    """
    return _envelope(
        "get_religion_overview",
        turn,
        asdict(status),
        {
            "religions": COVERAGE_COMPLETE,
            "players": COVERAGE_KNOWN_HISTORY,
        },
    )


def climate_envelope(
    *,
    turn: int | None,
    status: lq.ClimateOverview,
) -> dict[str, Any]:
    """get_climate_overview 单轨信封。

    当前气候状态为全集；``event_history`` 为已发生事实（KNOWN_HISTORY，
    仅含已揭示事件，迷雾中的事件坐标未知）。
    """
    return _envelope(
        "get_climate_overview",
        turn,
        asdict(status),
        {
            "climate": COVERAGE_COMPLETE,
            "event_history": COVERAGE_KNOWN_HISTORY,
        },
    )
