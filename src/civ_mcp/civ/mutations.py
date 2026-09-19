"""Domain mutation factories for the new Runtime Core.

They build one exact Civ6 request plus the evidence probe needed by
SessionKernel.  They never submit, retry, restart, or infer combat success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import re

from civ_mcp.civ.adapter import CivAdapter, CivMutationRequest
from civ_mcp.lua.cities import (
    build_city_attack,
    build_produce_item,
    build_purchase_item,
    build_resolve_city_capture,
    build_set_yield_focus,
)
from civ_mcp.lua.diplomacy import (
    build_diplomacy_respond,
    build_propose_trade,
    build_respond_to_deal,
)
from civ_mcp.lua.economy import build_make_trade_route
from civ_mcp.lua.governance import (
    build_appoint_governor,
    build_assign_governor,
    build_change_government,
    build_choose_dedication,
    build_promote_governor,
    build_promote_unit,
    build_send_envoy,
    build_set_policies,
    build_upgrade_unit,
)
from civ_mcp.lua.great_people import build_recruit_great_person
from civ_mcp.lua.espionage import build_spy_mission, build_spy_travel
from civ_mcp.lua.congress import build_congress_submit, build_congress_vote
from civ_mcp.lua.map import build_found_city, build_purchase_tile
from civ_mcp.lua.models import PendingDeal, TradeNegotiationState
from civ_mcp.lua.religion import build_choose_pantheon, build_found_religion, build_spread_religion
from civ_mcp.lua.notifications import build_end_turn
from civ_mcp.lua.tech import build_set_civic, build_set_research
from civ_mcp.lua.units import (
    build_attack_unit,
    build_builder_improvement,
    build_build_route,
    build_improve_tile,
    build_move_unit,
    build_remove_feature,
    build_remove_improvement,
    build_repair_improvement,
)
from civ_mcp.runtime.contracts import Evidence, OperationId, OperationIntent
from civ_mcp.runtime.session import MutationExecution, MutationPreconditionError


AttackReadback = Callable[[], Awaitable[Evidence | None]]
_TRADE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TRADE_AGREEMENTS = frozenset({"OPEN_BORDERS", "JOINT_WAR", "ALLIANCE"})


def _trade_terms(deal: PendingDeal) -> tuple[tuple[bool, str, str, int, int], ...]:
    """Create a stable comparison key for the exact pending deal terms."""
    return tuple(
        (
            item.is_from_us,
            item.item_type,
            item.name,
            item.amount,
            item.duration,
        )
        for item in (*deal.items_from_them, *deal.items_from_us)
    )


def _normalize_trade_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Validate and copy model input before it is interpolated into Lua."""
    normalized: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("交易条目必须是对象。")
        item_type = item.get("type")
        if not isinstance(item_type, str):
            raise ValueError("交易条目必须提供 type。")
        kind = item_type.upper()
        if kind not in {"GOLD", "RESOURCE", "FAVOR", "AGREEMENT", "CITY"}:
            raise ValueError(f"不支持的交易条目类型：{item_type}。")
        amount = item.get("amount", 0)
        duration = item.get("duration", 0)
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            or amount > 1_000_000
            or isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration < 0
            or duration > 100
        ):
            raise ValueError("交易数量和持续回合必须是合理的非负整数。")
        clean: dict[str, object] = {"type": kind, "amount": amount, "duration": duration}
        if kind == "RESOURCE":
            name = item.get("name")
            if (
                not isinstance(name, str)
                or not name.startswith("RESOURCE_")
                or not _TRADE_IDENTIFIER.fullmatch(name)
                or amount < 1
                or duration < 1
            ):
                raise ValueError("资源交易需要 RESOURCE_* 名称、正数量和正持续回合。")
            clean["name"] = name
        elif kind == "AGREEMENT":
            subtype = item.get("subtype")
            if not isinstance(subtype, str) or subtype not in _TRADE_AGREEMENTS:
                raise ValueError("协议交易 subtype 必须是 OPEN_BORDERS、JOINT_WAR 或 ALLIANCE。")
            clean["subtype"] = subtype
        elif kind == "CITY":
            city_id = item.get("city_id")
            if isinstance(city_id, bool) or not isinstance(city_id, int) or city_id < 0:
                raise ValueError("城市交易需要非负整数 city_id。")
            clean["city_id"] = city_id
        elif amount < 1:
            raise ValueError(f"{kind} 交易需要正数量。")
        normalized.append(clean)
    if not normalized:
        raise ValueError("交易至少需要一项给予或索取条目。")
    return normalized


class CivMutationFactory:
    """Build domain mutations; SessionKernel remains the only submitter."""

    def __init__(self, adapter: CivAdapter) -> None:
        self._adapter = adapter

    def move_unit(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        intent = OperationIntent.create(
            "move_unit",
            {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
        )

        async def verify() -> Evidence | None:
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception:
                return None
            for unit in units.value:
                if unit.unit_index == unit_index and (unit.x, unit.y) == (target_x, target_y):
                    return Evidence(
                        source="read_units",
                        observed_turn=units.observed_turn,
                        detail=f"unit_index={unit_index} at ({target_x},{target_y})",
                    )
            return None

        async def precheck() -> None:
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取移动前的单位 baseline。") from exc
            for unit in units.value:
                if unit.unit_index != unit_index:
                    continue
                if (unit.x, unit.y) == (target_x, target_y):
                    raise MutationPreconditionError("单位已在目标坐标，不提交重复移动。")
                return
            raise MutationPreconditionError("移动前找不到目标单位。")

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("move_unit", build_move_unit(unit_index, target_x, target_y)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def end_turn(
        self, *, operation_id: OperationId, readback: AttackReadback
    ) -> MutationExecution:
        """Build one end-turn request; TurnLoop owns waiting and interrupts."""
        return self._readback_action(
            operation_id=operation_id,
            tool="end_turn",
            arguments={},
            lua_code=build_end_turn(),
            readback=readback,
        )

    def attack_unit(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Attack a game-approved target and verify only a factual HP transition."""
        target = None

        async def precheck() -> None:
            nonlocal target
            try:
                result = await self._adapter.read_attack_target(
                    unit_index=unit_index,
                    target_x=target_x,
                    target_y=target_y,
                    observed_turn=observed_turn,
                )
            except Exception as exc:
                raise MutationPreconditionError("目标不是当前游戏允许的攻击对象。") from exc
            target = result.value[0]

        async def verify() -> Evidence | None:
            if target is None:
                return None
            try:
                observed = await self._adapter.read_combat_targets(
                    target_x=target_x, target_y=target_y, observed_turn=observed_turn
                )
            except Exception:
                return None
            current = next(
                (
                    item
                    for item in observed.value
                    if item.owner_id == target.owner_id
                    and item.unit_index == target.unit_index
                ),
                None,
            )
            if current is None:
                return Evidence(
                    "read_combat_targets",
                    observed.observed_turn,
                    f"target owner={target.owner_id} unit={target.unit_index} removed",
                )
            if current.health < target.health:
                return Evidence(
                    "read_combat_targets",
                    observed.observed_turn,
                    f"target owner={target.owner_id} unit={target.unit_index} hp {target.health}->{current.health}",
                )
            return None

        return MutationExecution(
            intent=OperationIntent.create(
                "attack_unit",
                {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("attack_unit", build_attack_unit(unit_index, target_x, target_y)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def attack_city(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Attack a game-approved city target and verify only HP loss/removal."""
        target = None

        async def precheck() -> None:
            nonlocal target
            try:
                result = await self._adapter.read_city_attack_target(
                    city_id=city_id,
                    target_x=target_x,
                    target_y=target_y,
                    observed_turn=observed_turn,
                )
            except Exception as exc:
                raise MutationPreconditionError("目标不是当前城市允许的攻击对象。") from exc
            target = result.value

        async def verify() -> Evidence | None:
            if target is None:
                return None
            try:
                observed = await self._adapter.read_combat_targets(
                    target_x=target_x, target_y=target_y, observed_turn=observed_turn
                )
            except Exception:
                return None
            current = next(
                (
                    item
                    for item in observed.value
                    if item.owner_id == target.owner_id
                    and item.unit_index == target.unit_index
                ),
                None,
            )
            if current is None:
                return Evidence(
                    "read_combat_targets",
                    observed.observed_turn,
                    f"target owner={target.owner_id} unit={target.unit_index} removed",
                )
            if current.health < target.health:
                return Evidence(
                    "read_combat_targets",
                    observed.observed_turn,
                    f"target owner={target.owner_id} unit={target.unit_index} hp {target.health}->{current.health}",
                )
            return None

        return MutationExecution(
            intent=OperationIntent.create(
                "attack_city",
                {"city_id": city_id, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("attack_city", build_city_attack(city_id, target_x, target_y)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def build_improvement(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        improvement_type: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Build a current-tile improvement with direct candidate and tile proof."""
        candidate = None

        async def precheck() -> None:
            nonlocal candidate
            try:
                candidates = await self._adapter.read_builder_improvement_candidates(
                    unit_index=unit_index,
                    observed_turn=observed_turn,
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取建设者当前格的改良候选。") from exc
            matches = [
                item
                for item in candidates.value
                if item.unit_index == unit_index and item.improvement_type == improvement_type
            ]
            if len(matches) != 1:
                raise MutationPreconditionError("目标不在建设者当前格的游戏允许候选中。")
            candidate = matches[0]

        async def verify() -> Evidence | None:
            if candidate is None:
                return None
            try:
                tile = await self._adapter.read_tile_improvement_state(
                    x=candidate.x,
                    y=candidate.y,
                    observed_turn=observed_turn,
                )
            except Exception:
                return None
            if tile.value.improvement_type != improvement_type or tile.value.is_pillaged:
                return None
            return Evidence(
                "read_tile_improvement_state",
                tile.observed_turn,
                f"unit_index={unit_index} built {improvement_type} at ({candidate.x},{candidate.y})",
            )

        return MutationExecution(
            intent=OperationIntent.create(
                "build_improvement",
                {"unit_index": unit_index, "improvement_type": improvement_type},
            ),
            request=CivMutationRequest(
                "build_improvement", build_builder_improvement(unit_index, improvement_type)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def set_production(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        item_type: str,
        item_name: str,
        observed_turn: int,
        target_x: int | None = None,
        target_y: int | None = None,
    ) -> MutationExecution:
        """Confirm production by the city's current queue, never Lua's OK text."""
        normalized_type = item_type.upper()
        intent = OperationIntent.create(
            "set_city_production",
            {
                "city_id": city_id,
                "item_type": normalized_type,
                "item_name": item_name,
                "target_x": target_x,
                "target_y": target_y,
            },
        )

        async def verify() -> Evidence | None:
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception:
                return None
            for city in cities.value:
                if city.city_id != city_id or city.currently_building != item_name:
                    continue
                if normalized_type == "DISTRICT":
                    if target_x is None or target_y is None:
                        return None
                    location = f"{item_name}@{target_x},{target_y}"
                    if location not in city.districts:
                        return None
                    return Evidence(
                        "read_cities",
                        cities.observed_turn,
                        f"city_id={city_id} producing {item_name} at ({target_x},{target_y})",
                    )
                return Evidence(
                    "read_cities",
                    cities.observed_turn,
                    f"city_id={city_id} producing {item_name}",
                )
            return None

        async def precheck() -> None:
            if normalized_type == "DISTRICT":
                if target_x is None or target_y is None:
                    raise MutationPreconditionError("区域生产必须提供游戏认可的格点坐标。")
                try:
                    placements = await self._adapter.read_district_placements(
                        city_id=city_id,
                        district_type=item_name,
                        observed_turn=observed_turn,
                    )
                except Exception as exc:
                    raise MutationPreconditionError("无法获取当前区域格点候选。") from exc
                if not any(
                    placement.district_type == item_name
                    and (placement.x, placement.y) == (target_x, target_y)
                    for placement in placements.value
                ):
                    raise MutationPreconditionError("目标不在当前游戏允许的区域格点候选中。")
                return
            if normalized_type not in {"UNIT", "BUILDING", "PROJECT"}:
                raise MutationPreconditionError("生产类型必须是 UNIT、BUILDING、DISTRICT 或 PROJECT。")
            if target_x is not None or target_y is not None:
                raise MutationPreconditionError("当前生产契约不接受格点坐标。")
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取生产队列 baseline。") from exc
            target_city = next((city for city in cities.value if city.city_id == city_id), None)
            if target_city is None:
                raise MutationPreconditionError("生产前找不到目标城市。")
            if target_city.currently_building == item_name:
                raise MutationPreconditionError("城市已在生产该项目，不提交重复设定。")
            try:
                candidates = await self._adapter.read_city_production(
                    city_id=city_id, observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取当前生产候选。") from exc
            if not any(
                option.category == normalized_type and option.item_name == item_name
                for option in candidates.value
            ):
                raise MutationPreconditionError("目标不在当前游戏允许的生产候选中。")

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "set_city_production",
                build_produce_item(city_id, normalized_type, item_name, target_x, target_y),
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def found_city(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Found at the observed settler tile, proving a newly created city exists."""
        intent = OperationIntent.create(
            "found_city",
            {
                "unit_index": unit_index,
                "target_x": target_x,
                "target_y": target_y,
            },
        )

        known_city_ids: frozenset[int] | None = None

        async def verify() -> Evidence | None:
            if known_city_ids is None:
                return None
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception:
                return None
            if any(
                city.city_id not in known_city_ids
                and (city.x, city.y) == (target_x, target_y)
                for city in cities.value
            ):
                return Evidence(
                    "read_cities",
                    cities.observed_turn,
                    f"new city at ({target_x},{target_y})",
                )
            return None

        async def precheck() -> None:
            nonlocal known_city_ids
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取建城前的城市 baseline。") from exc
            if any((city.x, city.y) == (target_x, target_y) for city in cities.value):
                raise MutationPreconditionError("目标坐标已有城市，不提交建城。")
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取建城前的单位 baseline。") from exc
            if not any(
                unit.unit_index == unit_index and (unit.x, unit.y) == (target_x, target_y)
                for unit in units.value
            ):
                raise MutationPreconditionError("定居者不在决策时的目标坐标，不提交建城。")
            known_city_ids = frozenset(city.city_id for city in cities.value)

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("found_city", build_found_city(unit_index)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def resolve_city_capture(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        choice: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Resolve one observed occupation choice with a domain postcondition."""
        normalized_choice = choice.upper()
        baseline = None
        intent = OperationIntent.create(
            "resolve_city_capture",
            {"city_id": city_id, "choice": normalized_choice},
        )

        async def precheck() -> None:
            nonlocal baseline
            try:
                capture = await self._adapter.read_pending_city_capture(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取城市占领选择 baseline。") from exc
            if capture.value is None or capture.value.city_id != city_id:
                raise MutationPreconditionError("当前没有匹配的城市占领选择，不提交操作。")
            if normalized_choice not in capture.value.allowed_choices:
                raise MutationPreconditionError("choice 不是游戏当前允许的城市占领选项。")
            baseline = capture.value

        async def verify() -> Evidence | None:
            if baseline is None:
                return None
            try:
                pending = await self._adapter.read_pending_city_capture(
                    observed_turn=observed_turn
                )
                if pending.value is not None and pending.value.city_id == city_id:
                    return None
                state = await self._adapter.read_city_capture_state(
                    x=baseline.x,
                    y=baseline.y,
                    observed_turn=observed_turn,
                )
            except Exception:
                return None
            if normalized_choice == "KEEP":
                if (
                    state.value.city_id == city_id
                    and state.value.owner_id == baseline.owner_id
                ):
                    return Evidence(
                        "read_pending_city_capture+read_city_capture_state",
                        state.observed_turn,
                        f"city_id={city_id} kept by player_id={baseline.owner_id}",
                    )
                return None
            if normalized_choice == "RAZE":
                if state.value.city_id is None:
                    return Evidence(
                        "read_pending_city_capture+read_city_capture_state",
                        state.observed_turn,
                        f"city_id={city_id} absent after raze",
                    )
                return None
            expected_owner = {
                "LIBERATE_FOUNDER": baseline.original_owner_id,
                "LIBERATE_PREVIOUS": baseline.previous_owner_id,
            }.get(normalized_choice)
            if expected_owner is not None and expected_owner >= 0:
                if state.value.city_id == city_id and state.value.owner_id == expected_owner:
                    return Evidence(
                        "read_pending_city_capture+read_city_capture_state",
                        state.observed_turn,
                        f"city_id={city_id} transferred to player_id={expected_owner}",
                    )
                return None
            if normalized_choice == "REJECT":
                return Evidence(
                    "read_pending_city_capture",
                    pending.observed_turn,
                    f"city_id={city_id} occupation choice closed after REJECT",
                )
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "resolve_city_capture", build_resolve_city_capture(normalized_choice.lower())
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def purchase_item(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        item_type: str,
        item_name: str,
        yield_type: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Purchase one live candidate with resource and object-state evidence.

        Candidate eligibility, currency baseline and object baseline are captured
        inside the precheck.  A caller cannot manufacture any of those facts.
        """
        normalized_type = item_type.upper()
        normalized_yield = yield_type.upper()
        intent = OperationIntent.create(
            "purchase_item",
            {
                "city_id": city_id,
                "item_type": normalized_type,
                "item_name": item_name,
                "yield_type": normalized_yield,
            },
        )
        currency_before: float | None = None
        known_unit_ids: frozenset[int] = frozenset()
        building_was_owned = False

        async def precheck() -> None:
            nonlocal currency_before, known_unit_ids, building_was_owned
            if normalized_type not in {"UNIT", "BUILDING"}:
                raise MutationPreconditionError("购买类型必须是 UNIT 或 BUILDING。")
            if normalized_yield not in {"YIELD_GOLD", "YIELD_FAITH"}:
                raise MutationPreconditionError("购买货币必须是 YIELD_GOLD 或 YIELD_FAITH。")
            try:
                purchases = await self._adapter.read_city_purchases(
                    city_id=city_id,
                    yield_type=normalized_yield,
                    observed_turn=observed_turn,
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取购买候选。") from exc

            candidates = [
                option
                for option in purchases.value
                if option.item_type == normalized_type and option.item_name == item_name
            ]
            if len(candidates) != 1:
                raise MutationPreconditionError("目标不在当前游戏允许的购买候选中。")
            candidate = candidates[0]
            try:
                overview = await self._adapter.read_overview()
            except Exception as exc:
                raise MutationPreconditionError("无法获取购买货币 baseline。") from exc
            balance = (
                overview.value.faith
                if normalized_yield == "YIELD_FAITH"
                else overview.value.gold
            )
            if balance < candidate.cost:
                raise MutationPreconditionError("当前货币不足以完成该购买。")
            currency_before = balance

            if normalized_type == "UNIT":
                try:
                    units = await self._adapter.read_units(observed_turn=observed_turn)
                except Exception as exc:
                    raise MutationPreconditionError("无法获取购买前的单位 baseline。") from exc
                known_unit_ids = frozenset(unit.unit_id for unit in units.value)
                return

            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取购买前的城市建筑 baseline。") from exc
            target_city = next((city for city in cities.value if city.city_id == city_id), None)
            if target_city is None:
                raise MutationPreconditionError("购买前找不到目标城市。")
            building_was_owned = item_name.removeprefix("BUILDING_") in target_city.buildings
            if building_was_owned:
                raise MutationPreconditionError("目标建筑已拥有，不提交重复购买。")

        async def verify() -> Evidence | None:
            if currency_before is None:
                return None
            try:
                overview = await self._adapter.read_overview()
                balance = (
                    overview.value.faith
                    if normalized_yield == "YIELD_FAITH"
                    else overview.value.gold
                )
                if balance >= currency_before:
                    return None
                if normalized_type == "UNIT":
                    units = await self._adapter.read_units(observed_turn=observed_turn)
                    if any(
                        unit.unit_id not in known_unit_ids and unit.unit_type == item_name
                        for unit in units.value
                    ):
                        return Evidence(
                            "read_overview+read_units",
                            overview.observed_turn,
                            f"purchased {item_name}",
                        )
                else:
                    cities = await self._adapter.read_cities(observed_turn=observed_turn)
                    building_name = item_name.removeprefix("BUILDING_")
                    if not building_was_owned and any(
                        city.city_id == city_id and building_name in city.buildings
                        for city in cities.value
                    ):
                        return Evidence(
                            "read_overview+read_cities",
                            overview.observed_turn,
                            f"purchased {item_name}",
                        )
            except Exception:
                return None
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "purchase_item",
                build_purchase_item(city_id, normalized_type, item_name, normalized_yield),
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def purchase_tile(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        x: int,
        y: int,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Buy one tile only when a fresh map/ownership readback proves it."""
        return self._readback_action(
            operation_id=operation_id,
            tool="purchase_tile",
            arguments={"city_id": city_id, "x": x, "y": y},
            lua_code=build_purchase_tile(city_id, x, y),
            readback=readback,
        )

    def set_city_focus(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        focus: str,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Change city focus only with a factual citizen-focus readback."""
        return self._readback_action(
            operation_id=operation_id,
            tool="set_city_focus",
            arguments={"city_id": city_id, "focus": focus},
            lua_code=build_set_yield_focus(city_id, focus),
            readback=readback,
        )

    def make_trade_route(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Start one live route candidate and confirm its exact trader/destination."""
        route_was_active = False

        async def precheck() -> None:
            nonlocal route_was_active
            try:
                destinations = await self._adapter.read_trade_destinations(
                    unit_index=unit_index, observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取商路目的地候选。") from exc
            if not any(
                destination.x == target_x and destination.y == target_y
                for destination in destinations.value
            ):
                raise MutationPreconditionError("目标不在当前游戏允许的商路目的地中。")
            try:
                routes = await self._adapter.read_trade_routes(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取商路 baseline。") from exc
            route_was_active = any(
                trader.unit_id == unit_index
                and trader.on_route
                and (trader.destination_x, trader.destination_y)
                == (target_x, target_y)
                for trader in routes.value.traders
            )
            if route_was_active:
                raise MutationPreconditionError("商人已在该目标城市运行商路，不重复提交。")

        async def verify() -> Evidence | None:
            if route_was_active:
                return None
            try:
                routes = await self._adapter.read_trade_routes(
                    observed_turn=observed_turn
                )
            except Exception:
                return None
            if any(
                trader.unit_id == unit_index
                and trader.on_route
                and (trader.destination_x, trader.destination_y)
                == (target_x, target_y)
                for trader in routes.value.traders
            ):
                return Evidence(
                    "read_trade_routes",
                    routes.observed_turn,
                    f"trader={unit_index} routed to ({target_x},{target_y})",
                )
            return None

        return MutationExecution(
            intent=OperationIntent.create(
                "make_trade_route",
                {
                    "unit_index": unit_index,
                    "target_x": target_x,
                    "target_y": target_y,
                },
            ),
            request=CivMutationRequest(
                "make_trade_route", build_make_trade_route(unit_index, target_x, target_y)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def improve_tile(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        improvement_name: str,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Build one improvement and require a later observed tile state."""
        return self._readback_action(
            operation_id=operation_id,
            tool="improve_tile",
            arguments={
                "unit_index": unit_index,
                "improvement_name": improvement_name,
            },
            lua_code=build_improve_tile(unit_index, improvement_name),
            readback=readback,
        )

    def repair_improvement(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        """Repair one tile only when a later map read proves it is restored."""
        return self._readback_action(
            operation_id=operation_id,
            tool="repair_improvement",
            arguments={"unit_index": unit_index},
            lua_code=build_repair_improvement(unit_index),
            readback=readback,
        )

    def remove_improvement(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        """Demolish one improvement; a send receipt cannot prove the tile changed."""
        return self._readback_action(
            operation_id=operation_id,
            tool="remove_improvement",
            arguments={"unit_index": unit_index},
            lua_code=build_remove_improvement(unit_index),
            readback=readback,
        )

    def remove_feature(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        """Harvest/chop a feature, closing only with a factual map readback."""
        return self._readback_action(
            operation_id=operation_id,
            tool="remove_feature",
            arguments={"unit_index": unit_index},
            lua_code=build_remove_feature(unit_index),
            readback=readback,
        )

    def build_route(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        """Build a road or railroad only with a later observed route state."""
        return self._readback_action(
            operation_id=operation_id,
            tool="build_route",
            arguments={"unit_index": unit_index},
            lua_code=build_build_route(unit_index),
            readback=readback,
        )

    def propose_trade(
        self,
        *,
        operation_id: OperationId,
        other_player_id: int,
        offer_items: list[dict[str, object]],
        request_items: list[dict[str, object]],
        observed_turn: int,
    ) -> MutationExecution:
        """Submit exact terms; absent direct counter-offer evidence stays UNKNOWN."""
        normalized_offer = _normalize_trade_items(offer_items) if offer_items else []
        normalized_request = _normalize_trade_items(request_items) if request_items else []
        if not normalized_offer and not normalized_request:
            raise ValueError("交易至少需要一项给予或索取条目。")

        async def precheck() -> None:
            try:
                deals = await self._adapter.read_pending_deals(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法确认当前没有同对象的待决交易。") from exc
            if any(deal.other_player_id == other_player_id for deal in deals.value):
                raise MutationPreconditionError("该文明已有待决交易，不覆盖或自动处理。")

        async def verify() -> Evidence | None:
            try:
                negotiation = await self._adapter.read_trade_negotiation(
                    other_player_id=other_player_id,
                    observed_turn=observed_turn,
                )
            except Exception:
                return None
            if negotiation.value.state is TradeNegotiationState.PROPOSED:
                return Evidence(
                    "read_trade_negotiation",
                    negotiation.observed_turn,
                    f"PROPOSED to player_id={other_player_id}; awaiting direct response evidence",
                )
            if negotiation.value.state is not TradeNegotiationState.COUNTER_OFFER:
                return None
            try:
                deals = await self._adapter.read_pending_deals(observed_turn=observed_turn)
            except Exception:
                return None
            matches = [deal for deal in deals.value if deal.other_player_id == other_player_id]
            if len(matches) != 1:
                return None
            deal = matches[0]
            if not deal.items_from_them and not deal.items_from_us:
                return None
            return Evidence(
                "read_pending_deals",
                deals.observed_turn,
                f"COUNTER_OFFER from player_id={other_player_id}; model must inspect exact terms",
            )

        return MutationExecution(
            intent=OperationIntent.create(
                "propose_trade",
                {
                    "other_player_id": other_player_id,
                    "offer_items": normalized_offer,
                    "request_items": normalized_request,
                },
            ),
            request=CivMutationRequest(
                "propose_trade",
                build_propose_trade(other_player_id, normalized_offer, normalized_request),
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def respond_to_trade_offer(
        self,
        *,
        operation_id: OperationId,
        other_player_id: int,
        choice: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Resolve one exact pending deal; closure, not Lua prose, is evidence.

        A closed deal proves only that the selected response resolved this
        observed offer.  It does not invent a broader diplomatic outcome.
        """
        normalized_choice = choice.upper()
        if normalized_choice not in {"ACCEPT", "REJECT"}:
            raise ValueError("交易回应只能是 ACCEPT 或 REJECT。")
        baseline = None

        async def precheck() -> None:
            nonlocal baseline
            try:
                deals = await self._adapter.read_pending_deals(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取待决交易的实际条款。") from exc
            matches = [
                deal for deal in deals.value if deal.other_player_id == other_player_id
            ]
            if len(matches) != 1:
                raise MutationPreconditionError("当前不存在唯一匹配的待决交易，不提交回应。")
            deal = matches[0]
            if not deal.items_from_them and not deal.items_from_us:
                raise MutationPreconditionError("待决交易缺少可验证条款，不提交回应。")
            baseline = _trade_terms(deal)

        async def verify() -> Evidence | None:
            if baseline is None:
                return None
            try:
                deals = await self._adapter.read_pending_deals(observed_turn=observed_turn)
            except Exception:
                return None
            matches = [
                deal for deal in deals.value if deal.other_player_id == other_player_id
            ]
            if not matches:
                return Evidence(
                    "read_pending_deals",
                    deals.observed_turn,
                    f"{normalized_choice} player_id={other_player_id} pending deal closed",
                )
            if len(matches) == 1 and _trade_terms(matches[0]) != baseline:
                return Evidence(
                    "read_pending_deals",
                    deals.observed_turn,
                    f"player_id={other_player_id} pending deal terms changed after {normalized_choice}",
                )
            return None

        return MutationExecution(
            intent=OperationIntent.create(
                "respond_to_trade_offer",
                {"other_player_id": other_player_id, "choice": normalized_choice},
            ),
            request=CivMutationRequest(
                "respond_to_trade_offer",
                build_respond_to_deal(
                    other_player_id, accept=normalized_choice == "ACCEPT"
                ),
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def submit_congress(
        self, *, operation_id: OperationId, observed_turn: int
    ) -> MutationExecution:
        """Submit an explicitly abstaining congress session without ending turn anew."""

        async def precheck() -> None:
            try:
                status = await self._adapter.read_world_congress(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取世界议会当前状态。") from exc
            if not status.value.is_in_session:
                raise MutationPreconditionError("当前没有可提交的世界议会会话。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_world_congress(
                    observed_turn=observed_turn
                )
            except Exception:
                return None
            if status.value.is_in_session:
                return None
            return Evidence(
                "read_world_congress",
                status.observed_turn,
                "world congress session closed after abstaining submission",
            )

        return MutationExecution(
            intent=OperationIntent.create("submit_congress", {"choice": "SUBMIT_ABSTAIN"}),
            request=CivMutationRequest(
                "submit_congress", build_congress_submit(resume_pending=True)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def respond_to_diplomacy(
        self,
        *,
        operation_id: OperationId,
        other_player_id: int,
        response: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Respond once to the currently observed diplomacy session.

        A response is confirmed only when a fresh session read shows the
        selected session closed or progressed.  Repeated or unchanged UI state
        remains ``UNKNOWN`` rather than being inferred from Lua output text.
        """
        normalized_response = response.upper()
        if normalized_response not in {"POSITIVE", "NEGATIVE", "EXIT"}:
            raise ValueError("外交会话 response 只能是 POSITIVE、NEGATIVE 或 EXIT。")
        intent = OperationIntent.create(
            "respond_to_diplomacy",
            {"other_player_id": other_player_id, "response": normalized_response},
        )
        baseline: tuple[int, str, str, str, str] | None = None

        async def precheck() -> None:
            nonlocal baseline
            try:
                sessions = await self._adapter.read_diplomacy_sessions(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取外交会话 baseline。") from exc
            matches = [
                session
                for session in sessions.value
                if session.other_player_id == other_player_id
            ]
            if len(matches) != 1:
                raise MutationPreconditionError("当前不存在唯一匹配的外交会话，不提交响应。")
            session = matches[0]
            baseline = (
                session.session_id,
                session.dialogue_text,
                session.reason_text,
                session.buttons,
                session.deal_summary,
            )

        async def verify() -> Evidence | None:
            if baseline is None:
                return None
            try:
                sessions = await self._adapter.read_diplomacy_sessions(
                    observed_turn=observed_turn
                )
            except Exception:
                return None
            matches = [
                session
                for session in sessions.value
                if session.other_player_id == other_player_id
            ]
            if not matches:
                return Evidence(
                    "read_diplomacy_sessions",
                    sessions.observed_turn,
                    f"player_id={other_player_id} session closed after {normalized_response}",
                )
            session = matches[0]
            current = (
                session.session_id,
                session.dialogue_text,
                session.reason_text,
                session.buttons,
                session.deal_summary,
            )
            if current != baseline:
                return Evidence(
                    "read_diplomacy_sessions",
                    sessions.observed_turn,
                    f"player_id={other_player_id} session advanced after {normalized_response}",
                )
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "respond_to_diplomacy",
                build_diplomacy_respond(other_player_id, normalized_response),
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def set_research(
        self, *, operation_id: OperationId, tech_name: str, observed_turn: int
    ) -> MutationExecution:
        """Set one legal technology with a typed pre/post research observation."""
        self._require_gameinfo_type(tech_name, "TECH_")
        intent = OperationIntent.create("set_research", {"tech_name": tech_name})

        async def precheck() -> None:
            try:
                status = await self._adapter.read_tech_civics(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取科研选择 baseline。") from exc
            if status.value.current_research_type == tech_name:
                raise MutationPreconditionError("当前已在研究该科技，不提交重复设定。")
            if tech_name not in {tech.tech_type for tech in status.value.available_techs}:
                raise MutationPreconditionError("目标科技不是当前可研究候选，不提交设定。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_tech_civics(observed_turn=observed_turn)
            except Exception:
                return None
            if status.value.current_research_type != tech_name:
                return None
            return Evidence(
                "read_tech_civics",
                status.observed_turn,
                f"current_research_type={tech_name}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("set_research", build_set_research(tech_name)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def set_civic(
        self, *, operation_id: OperationId, civic_name: str, observed_turn: int
    ) -> MutationExecution:
        """Set one legal civic with a typed pre/post civic observation."""
        self._require_gameinfo_type(civic_name, "CIVIC_")
        intent = OperationIntent.create("set_civic", {"civic_name": civic_name})

        async def precheck() -> None:
            try:
                status = await self._adapter.read_tech_civics(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取市政选择 baseline。") from exc
            if status.value.current_civic_type == civic_name:
                raise MutationPreconditionError("当前已在推进该市政，不提交重复设定。")
            if civic_name not in {civic.civic_type for civic in status.value.available_civics}:
                raise MutationPreconditionError("目标市政不是当前可推进候选，不提交设定。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_tech_civics(observed_turn=observed_turn)
            except Exception:
                return None
            if status.value.current_civic_type != civic_name:
                return None
            return Evidence(
                "read_tech_civics",
                status.observed_turn,
                f"current_civic_type={civic_name}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("set_civic", build_set_civic(civic_name)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def set_policies(
        self,
        *,
        operation_id: OperationId,
        assignments: dict[int, str],
        observed_turn: int,
    ) -> MutationExecution:
        """Set only currently legal policy candidates and prove every touched slot."""
        if not assignments:
            raise ValueError("assignments 不能为空。")
        normalized_assignments = {
            slot_index: policy_type.upper()
            for slot_index, policy_type in assignments.items()
        }
        for policy_type in normalized_assignments.values():
            if policy_type != "NONE":
                self._require_gameinfo_type(policy_type, "POLICY_")
        requested_policies = [
            policy_type
            for policy_type in normalized_assignments.values()
            if policy_type != "NONE"
        ]
        if len(requested_policies) != len(set(requested_policies)):
            raise ValueError("同一政策不能同时放入多个槽位。")
        intent = OperationIntent.create(
            "set_policies", {"assignments": normalized_assignments}
        )

        async def precheck() -> None:
            try:
                status = await self._adapter.read_policies(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取政策配置 baseline。") from exc
            slots = {slot.slot_index: slot for slot in status.value.slots}
            candidates = {
                policy.policy_type: policy for policy in status.value.available_policies
            }
            changed = False
            for slot_index, policy_type in normalized_assignments.items():
                slot = slots.get(slot_index)
                if slot is None:
                    raise MutationPreconditionError("目标不是当前政府的政策槽位。")
                expected = None if policy_type == "NONE" else policy_type
                if slot.current_policy != expected:
                    changed = True
                if expected is None:
                    continue
                candidate = candidates.get(expected)
                if candidate is None or slot_index not in candidate.eligible_slots:
                    raise MutationPreconditionError("目标政策不是该槽位当前的合法候选。")
            if not changed:
                raise MutationPreconditionError("所有目标槽位已是请求状态，不提交重复配置。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_policies(observed_turn=observed_turn)
            except Exception:
                return None
            slots = {slot.slot_index: slot for slot in status.value.slots}
            for slot_index, policy_type in normalized_assignments.items():
                slot = slots.get(slot_index)
                expected = None if policy_type == "NONE" else policy_type
                if slot is None or slot.current_policy != expected:
                    return None
            assignment_detail = ", ".join(
                f"{slot_index}={policy_type}"
                for slot_index, policy_type in sorted(normalized_assignments.items())
            )
            return Evidence(
                "read_policies",
                status.observed_turn,
                f"policy_slots={assignment_detail}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "set_policies", build_set_policies(normalized_assignments)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def change_government(
        self, *, operation_id: OperationId, government_type: str, observed_turn: int
    ) -> MutationExecution:
        """Change to one unlocked government, confirmed by its active-state readback."""
        self._require_gameinfo_type(government_type, "GOVERNMENT_")
        intent = OperationIntent.create(
            "change_government", {"government_type": government_type}
        )
        baseline_type: str | None = None

        async def precheck() -> None:
            nonlocal baseline_type
            try:
                governments = await self._adapter.read_governments(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取切换政府 baseline。") from exc
            current = [government for government in governments.value if government.is_current]
            targets = [
                government
                for government in governments.value
                if government.government_type == government_type
            ]
            if len(current) != 1 or len(targets) != 1:
                raise MutationPreconditionError("当前政府或目标政府 identity 不唯一。")
            if targets[0].is_current:
                raise MutationPreconditionError("目标已经是当前政府，不提交重复切换。")
            baseline_type = current[0].government_type

        async def verify() -> Evidence | None:
            if baseline_type is None:
                return None
            try:
                governments = await self._adapter.read_governments(
                    observed_turn=observed_turn
                )
            except Exception:
                return None
            current = [government for government in governments.value if government.is_current]
            if len(current) != 1 or current[0].government_type != government_type:
                return None
            return Evidence(
                "read_governments",
                governments.observed_turn,
                f"government_type={baseline_type}->{government_type}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "change_government", build_change_government(government_type)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def appoint_governor(
        self, *, operation_id: OperationId, governor_type: str, observed_turn: int
    ) -> MutationExecution:
        """Appoint one currently eligible governor with a point-delta readback."""
        self._require_gameinfo_type(governor_type, "GOVERNOR_")
        intent = OperationIntent.create("appoint_governor", {"governor_type": governor_type})
        baseline_points: int | None = None

        async def precheck() -> None:
            nonlocal baseline_points
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取任命总督 baseline。") from exc
            if not status.value.can_appoint or status.value.points_available <= 0:
                raise MutationPreconditionError("当前没有可用于任命总督的点数。")
            if any(
                governor.governor_type == governor_type
                for governor in status.value.appointed
            ):
                raise MutationPreconditionError("目标总督已经任命，不提交重复任命。")
            if governor_type not in {
                governor.governor_type for governor in status.value.available_to_appoint
            }:
                raise MutationPreconditionError("目标不是当前可任命的总督。")
            baseline_points = status.value.points_available

        async def verify() -> Evidence | None:
            if baseline_points is None:
                return None
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception:
                return None
            if (
                status.value.points_available != baseline_points - 1
                or not any(
                    governor.governor_type == governor_type
                    for governor in status.value.appointed
                )
            ):
                return None
            return Evidence(
                "read_governors",
                status.observed_turn,
                f"governor_type={governor_type} appointed; points {baseline_points}->{status.value.points_available}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "appoint_governor", build_appoint_governor(governor_type)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def assign_governor(
        self,
        *,
        operation_id: OperationId,
        governor_type: str,
        city_id: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Assign an appointed governor to one current local city."""
        self._require_gameinfo_type(governor_type, "GOVERNOR_")
        intent = OperationIntent.create(
            "assign_governor", {"governor_type": governor_type, "city_id": city_id}
        )

        async def precheck() -> None:
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取派驻总督 baseline。") from exc
            matches = [
                governor
                for governor in status.value.appointed
                if governor.governor_type == governor_type
            ]
            if len(matches) != 1:
                raise MutationPreconditionError("目标总督当前未任命或 identity 不唯一。")
            if matches[0].assigned_city_id == city_id:
                raise MutationPreconditionError("目标总督已派驻在该城市，不提交重复派驻。")
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取派驻目标城市 baseline。") from exc
            if not any(city.city_id == city_id for city in cities.value):
                raise MutationPreconditionError("目标不是当前本地城市，不提交派驻。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception:
                return None
            if not any(
                governor.governor_type == governor_type
                and governor.assigned_city_id == city_id
                for governor in status.value.appointed
            ):
                return None
            return Evidence(
                "read_governors",
                status.observed_turn,
                f"governor_type={governor_type} assigned_city_id={city_id}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "assign_governor", build_assign_governor(governor_type, city_id)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def promote_governor(
        self,
        *,
        operation_id: OperationId,
        governor_type: str,
        promotion_type: str,
        observed_turn: int,
    ) -> MutationExecution:
        """Promote one currently legal governor choice with a point-delta readback."""
        self._require_gameinfo_type(governor_type, "GOVERNOR_")
        self._require_gameinfo_type(promotion_type, "GOVERNOR_PROMOTION_")
        intent = OperationIntent.create(
            "promote_governor",
            {"governor_type": governor_type, "promotion_type": promotion_type},
        )
        baseline_points: int | None = None

        async def precheck() -> None:
            nonlocal baseline_points
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取总督晋升 baseline。") from exc
            matches = [
                governor
                for governor in status.value.appointed
                if governor.governor_type == governor_type
            ]
            if len(matches) != 1:
                raise MutationPreconditionError("目标总督当前未任命或 identity 不唯一。")
            if promotion_type not in {
                promotion.promotion_type for promotion in matches[0].eligible_promotions
            }:
                raise MutationPreconditionError("目标不是当前可选的总督晋升。")
            baseline_points = status.value.points_available

        async def verify() -> Evidence | None:
            if baseline_points is None:
                return None
            try:
                status = await self._adapter.read_governors(observed_turn=observed_turn)
            except Exception:
                return None
            matches = [
                governor
                for governor in status.value.appointed
                if governor.governor_type == governor_type
            ]
            if (
                len(matches) != 1
                or status.value.points_available != baseline_points - 1
                or promotion_type not in matches[0].owned_promotions
            ):
                return None
            return Evidence(
                "read_governors",
                status.observed_turn,
                (
                    f"governor_type={governor_type} owns={promotion_type}; points "
                    f"{baseline_points}->{status.value.points_available}"
                ),
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "promote_governor", build_promote_governor(governor_type, promotion_type)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def promote_unit(
        self, *, operation_id: OperationId, unit_index: int, promotion_type: str, observed_turn: int
    ) -> MutationExecution:
        """Promote one eligible unit, confirmed by its owned promotion type."""
        self._require_gameinfo_type(promotion_type, "PROMOTION_")
        intent = OperationIntent.create(
            "promote_unit",
            {"unit_index": unit_index, "promotion_type": promotion_type},
        )

        async def precheck() -> None:
            try:
                status = await self._adapter.read_unit_promotions(
                    unit_index=unit_index, observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取单位晋升 baseline。") from exc
            if status.value.unit_index != unit_index:
                raise MutationPreconditionError("晋升前读取的单位 identity 不匹配。")
            if promotion_type not in {
                promotion.promotion_type for promotion in status.value.promotions
            }:
                raise MutationPreconditionError("目标不是当前可选的单位晋升。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_unit_promotions(
                    unit_index=unit_index, observed_turn=observed_turn
                )
            except Exception:
                return None
            if promotion_type not in status.value.owned_promotions:
                return None
            return Evidence(
                "read_unit_promotions",
                status.observed_turn,
                f"unit_index={unit_index} owns={promotion_type}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "promote_unit",
                build_promote_unit(unit_index, promotion_type),
                context="gamecore",
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def upgrade_unit(
        self, *, operation_id: OperationId, unit_index: int, observed_turn: int
    ) -> MutationExecution:
        """Upgrade one eligible unit, confirmed by its observed target type."""
        intent = OperationIntent.create("upgrade_unit", {"unit_index": unit_index})
        expected_type: str | None = None

        async def precheck() -> None:
            nonlocal expected_type
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取单位升级 baseline。") from exc
            matches = [unit for unit in units.value if unit.unit_index == unit_index]
            if len(matches) != 1:
                raise MutationPreconditionError("升级前找不到唯一目标单位。")
            unit = matches[0]
            if not unit.can_upgrade or not unit.upgrade_target:
                raise MutationPreconditionError("目标单位当前不可升级，不提交操作。")
            expected_type = unit.upgrade_target

        async def verify() -> Evidence | None:
            if expected_type is None:
                return None
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception:
                return None
            if not any(
                unit.unit_index == unit_index and unit.unit_type == expected_type
                for unit in units.value
            ):
                return None
            return Evidence(
                "read_units",
                units.observed_turn,
                f"unit_index={unit_index} upgraded_to={expected_type}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("upgrade_unit", build_upgrade_unit(unit_index)),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def send_envoy(
        self,
        *,
        operation_id: OperationId,
        city_state_player_id: int,
        observed_turn: int,
    ) -> MutationExecution:
        """Send one currently available envoy and prove both resulting deltas."""
        intent = OperationIntent.create(
            "send_envoy", {"city_state_player_id": city_state_player_id}
        )
        baseline_tokens: int | None = None
        baseline_envoys: int | None = None

        async def precheck() -> None:
            nonlocal baseline_tokens, baseline_envoys
            try:
                status = await self._adapter.read_city_states(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取派遣使者 baseline。") from exc
            if status.value.tokens_available <= 0:
                raise MutationPreconditionError("当前没有可用使者，不提交派遣。")
            matches = [
                city_state
                for city_state in status.value.city_states
                if city_state.player_id == city_state_player_id
            ]
            if len(matches) != 1 or not matches[0].can_send_envoy:
                raise MutationPreconditionError("目标城邦当前不可接收使者，不提交派遣。")
            baseline_tokens = status.value.tokens_available
            baseline_envoys = matches[0].envoys_sent

        async def verify() -> Evidence | None:
            if baseline_tokens is None or baseline_envoys is None:
                return None
            try:
                status = await self._adapter.read_city_states(observed_turn=observed_turn)
            except Exception:
                return None
            matches = [
                city_state
                for city_state in status.value.city_states
                if city_state.player_id == city_state_player_id
            ]
            if (
                len(matches) != 1
                or status.value.tokens_available != baseline_tokens - 1
                or matches[0].envoys_sent != baseline_envoys + 1
            ):
                return None
            return Evidence(
                "read_city_states",
                status.observed_turn,
                (
                    f"player_id={city_state_player_id} envoys "
                    f"{baseline_envoys}->{matches[0].envoys_sent}; tokens "
                    f"{baseline_tokens}->{status.value.tokens_available}"
                ),
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "send_envoy", build_send_envoy(city_state_player_id)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def choose_dedication(
        self, *, operation_id: OperationId, dedication_index: int, observed_turn: int
    ) -> MutationExecution:
        """Choose one currently legal commemoration, proved by its active type."""
        intent = OperationIntent.create(
            "choose_dedication", {"dedication_index": dedication_index}
        )
        selected_name: str | None = None

        async def precheck() -> None:
            nonlocal selected_name
            try:
                status = await self._adapter.read_dedications(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取时代着力点 baseline。") from exc
            if status.value.selections_allowed <= 0:
                raise MutationPreconditionError("当前不需要选择时代着力点。")
            choices = {
                choice.index: choice.name for choice in status.value.choices
            }
            try:
                selected_name = choices[dedication_index]
            except KeyError as exc:
                raise MutationPreconditionError("目标不是当前可选的时代着力点。") from exc
            if selected_name in status.value.active:
                raise MutationPreconditionError("该时代着力点已经生效，不提交重复选择。")

        async def verify() -> Evidence | None:
            if selected_name is None:
                return None
            try:
                status = await self._adapter.read_dedications(observed_turn=observed_turn)
            except Exception:
                return None
            if selected_name not in status.value.active:
                return None
            return Evidence(
                "read_dedications",
                status.observed_turn,
                f"active commemoration={selected_name}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "choose_dedication", build_choose_dedication(dedication_index)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def choose_pantheon(
        self, *, operation_id: OperationId, belief_type: str, observed_turn: int
    ) -> MutationExecution:
        """Found one currently legal pantheon, proved by its stable belief ID."""
        self._require_gameinfo_type(belief_type, "BELIEF_")
        intent = OperationIntent.create("choose_pantheon", {"belief_type": belief_type})

        async def precheck() -> None:
            try:
                status = await self._adapter.read_pantheon_status(
                    observed_turn=observed_turn
                )
            except Exception as exc:
                raise MutationPreconditionError("无法获取万神殿选择 baseline。") from exc
            if status.value.has_pantheon:
                raise MutationPreconditionError("当前已拥有万神殿，不提交重复选择。")
            if belief_type not in {
                belief.belief_type for belief in status.value.available_beliefs
            }:
                raise MutationPreconditionError("目标信条不是当前可选的万神殿候选。")
            if (
                status.value.pantheon_cost > 0
                and status.value.faith_balance < status.value.pantheon_cost
            ):
                raise MutationPreconditionError("当前信仰不足以创立万神殿。")

        async def verify() -> Evidence | None:
            try:
                status = await self._adapter.read_pantheon_status(
                    observed_turn=observed_turn
                )
            except Exception:
                return None
            if status.value.current_belief != belief_type:
                return None
            return Evidence(
                "read_pantheon_status",
                status.observed_turn,
                f"current_belief={belief_type}",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "choose_pantheon", build_choose_pantheon(belief_type)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def found_religion(
        self, *, operation_id: OperationId, religion_type: str, follower_belief: str, founder_belief: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="found_religion", arguments={"religion_type": religion_type, "follower_belief": follower_belief, "founder_belief": founder_belief}, lua_code=build_found_religion(religion_type, follower_belief, founder_belief), readback=readback)

    def spread_religion(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="spread_religion", arguments={"unit_index": unit_index}, lua_code=build_spread_religion(unit_index), readback=readback)

    def recruit_great_person(
        self, *, operation_id: OperationId, individual_id: int, observed_turn: int
    ) -> MutationExecution:
        """Recruit one game-legal individual, confirmed by its local claim state."""
        intent = OperationIntent.create(
            "recruit_great_person", {"individual_id": individual_id}
        )

        async def precheck() -> None:
            try:
                people = await self._adapter.read_great_people(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取大人物招募 baseline。") from exc
            matches = [
                person for person in people.value if person.individual_id == individual_id
            ]
            if len(matches) != 1 or not matches[0].can_recruit:
                raise MutationPreconditionError("目标大人物当前不可招募，不提交操作。")

        async def verify() -> Evidence | None:
            try:
                people = await self._adapter.read_great_people(observed_turn=observed_turn)
            except Exception:
                return None
            if not any(
                person.individual_id == individual_id and person.claimed_by_local
                for person in people.value
            ):
                return None
            return Evidence(
                "read_great_people",
                people.observed_turn,
                f"individual_id={individual_id} claimed_by_local=true",
            )

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "recruit_great_person", build_recruit_great_person(individual_id)
            ),
            verify=verify,
            operation_id=operation_id,
            precheck=precheck,
        )

    def spy_travel(
        self, *, operation_id: OperationId, unit_index: int, target_x: int, target_y: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="spy_travel", arguments={"unit_index": unit_index, "target_x": target_x, "target_y": target_y}, lua_code=build_spy_travel(unit_index, target_x, target_y), readback=readback)

    def spy_mission(
        self, *, operation_id: OperationId, unit_index: int, mission_type: str, target_x: int, target_y: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="spy_mission", arguments={"unit_index": unit_index, "mission_type": mission_type, "target_x": target_x, "target_y": target_y}, lua_code=build_spy_mission(unit_index, mission_type, target_x, target_y), readback=readback)

    def congress_vote(
        self, *, operation_id: OperationId, resolution_hash: int, option: int, target_index: int, num_votes: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="congress_vote", arguments={"resolution_hash": resolution_hash, "option": option, "target_index": target_index, "num_votes": num_votes}, lua_code=build_congress_vote(resolution_hash, option, target_index, num_votes), readback=readback)

    def congress_submit(
        self, *, operation_id: OperationId, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="congress_submit", arguments={}, lua_code=build_congress_submit(resume_pending=True), readback=readback)

    @staticmethod
    def _require_gameinfo_type(value: str, prefix: str) -> None:
        if not re.fullmatch(rf"{prefix}[A-Z0-9_]+", value):
            raise ValueError(f"{prefix} 目标必须是稳定的 GameInfo type ID。")

    @staticmethod
    def _readback_action(
        *,
        operation_id: OperationId,
        tool: str,
        arguments: dict[str, object],
        lua_code: str,
        readback: AttackReadback,
        context: str = "ingame",
    ) -> MutationExecution:
        """Shared wiring only; each caller owns its domain-specific evidence."""
        return MutationExecution(
            intent=OperationIntent.create(tool, arguments),
            request=CivMutationRequest(tool, lua_code, context=context),
            verify=readback,
            operation_id=operation_id,
        )
