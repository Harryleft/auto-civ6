"""Domain mutation factories for the new Runtime Core.

They build one exact Civ6 request plus the evidence probe needed by
SessionKernel.  They never submit, retry, restart, or infer combat success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from civ_mcp.civ.adapter import CivAdapter, CivMutationRequest
from civ_mcp.lua.cities import (
    build_city_attack,
    build_produce_item,
    build_purchase_item,
    build_set_yield_focus,
)
from civ_mcp.lua.diplomacy import build_diplomacy_respond, build_propose_trade
from civ_mcp.lua.economy import build_make_trade_route
from civ_mcp.lua.governance import (
    build_appoint_governor,
    build_assign_governor,
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
from civ_mcp.lua.religion import build_choose_pantheon, build_found_religion, build_spread_religion
from civ_mcp.lua.notifications import build_end_turn
from civ_mcp.lua.tech import build_set_civic, build_set_research
from civ_mcp.lua.units import (
    build_attack_unit,
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
        readback: AttackReadback,
    ) -> MutationExecution:
        """Create an attack whose caller supplies a factual postcondition probe."""
        return MutationExecution(
            intent=OperationIntent.create(
                "attack_unit",
                {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("attack_unit", build_attack_unit(unit_index, target_x, target_y)),
            verify=readback,
            operation_id=operation_id,
        )

    def attack_city(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        target_x: int,
        target_y: int,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Request one city attack; combat outcome still needs factual readback."""
        return self._readback_action(
            operation_id=operation_id,
            tool="city_attack",
            arguments={
                "city_id": city_id,
                "target_x": target_x,
                "target_y": target_y,
            },
            lua_code=build_city_attack(city_id, target_x, target_y),
            readback=readback,
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
        intent = OperationIntent.create(
            "set_city_production",
            {
                "city_id": city_id,
                "item_type": item_type,
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
                if city.city_id == city_id and city.currently_building == item_name:
                    return Evidence("read_cities", cities.observed_turn, f"city_id={city_id} producing {item_name}")
            return None

        async def precheck() -> None:
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception as exc:
                raise MutationPreconditionError("无法获取生产队列 baseline。") from exc
            for city in cities.value:
                if city.city_id != city_id:
                    continue
                if city.currently_building == item_name:
                    raise MutationPreconditionError("城市已在生产该项目，不提交重复设定。")
                return
            raise MutationPreconditionError("生产前找不到目标城市。")

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "set_city_production",
                build_produce_item(city_id, item_type, item_name, target_x, target_y),
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

    def purchase_item(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        item_type: str,
        item_name: str,
        yield_type: str,
        currency_before: float,
        observed_turn: int,
        known_unit_ids: frozenset[int] = frozenset(),
    ) -> MutationExecution:
        """Confirm purchase with both resource decrease and a new owned object."""
        intent = OperationIntent.create(
            "purchase_item",
            {"city_id": city_id, "item_type": item_type, "item_name": item_name, "yield_type": yield_type},
        )

        async def verify() -> Evidence | None:
            try:
                overview = await self._adapter.read_overview()
                balance = overview.value.faith if yield_type == "YIELD_FAITH" else overview.value.gold
                if balance >= currency_before:
                    return None
                if item_type.upper() == "UNIT":
                    units = await self._adapter.read_units(observed_turn=observed_turn)
                    if any(unit.unit_id not in known_unit_ids and unit.unit_type == item_name for unit in units.value):
                        return Evidence("read_overview+read_units", overview.observed_turn, f"purchased {item_name}")
                else:
                    cities = await self._adapter.read_cities(observed_turn=observed_turn)
                    if any(city.city_id == city_id and item_name in city.buildings for city in cities.value):
                        return Evidence("read_overview+read_cities", overview.observed_turn, f"purchased {item_name}")
            except Exception:
                return None
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("purchase_item", build_purchase_item(city_id, item_type, item_name, yield_type)),
            verify=verify,
            operation_id=operation_id,
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
        readback: AttackReadback,
    ) -> MutationExecution:
        """A route is confirmed only by a domain route readback supplied by caller."""
        return MutationExecution(
            intent=OperationIntent.create(
                "make_trade_route", {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("make_trade_route", build_make_trade_route(unit_index, target_x, target_y)),
            verify=readback,
            operation_id=operation_id,
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
        readback: AttackReadback,
    ) -> MutationExecution:
        """Submit exactly these terms; a counter-offer is never auto-accepted."""
        return MutationExecution(
            intent=OperationIntent.create(
                "propose_trade",
                {
                    "other_player_id": other_player_id,
                    "offer_items": offer_items,
                    "request_items": request_items,
                },
            ),
            request=CivMutationRequest(
                "propose_trade",
                build_propose_trade(other_player_id, offer_items, request_items),
            ),
            verify=readback,
            operation_id=operation_id,
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
        self, *, operation_id: OperationId, tech_name: str, readback: AttackReadback
    ) -> MutationExecution:
        """Set research only after a fresh tech/civic readback can confirm it."""
        return self._readback_action(
            operation_id=operation_id,
            tool="set_research",
            arguments={"tech_name": tech_name},
            lua_code=build_set_research(tech_name),
            readback=readback,
        )

    def set_civic(
        self, *, operation_id: OperationId, civic_name: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(
            operation_id=operation_id,
            tool="set_civic",
            arguments={"civic_name": civic_name},
            lua_code=build_set_civic(civic_name),
            readback=readback,
        )

    def set_policies(
        self, *, operation_id: OperationId, assignments: dict[int, str], readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="set_policies", arguments={"assignments": assignments}, lua_code=build_set_policies(assignments), readback=readback)

    def appoint_governor(
        self, *, operation_id: OperationId, governor_type: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="appoint_governor", arguments={"governor_type": governor_type}, lua_code=build_appoint_governor(governor_type), readback=readback)

    def assign_governor(
        self, *, operation_id: OperationId, governor_type: str, city_id: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="assign_governor", arguments={"governor_type": governor_type, "city_id": city_id}, lua_code=build_assign_governor(governor_type, city_id), readback=readback)

    def promote_governor(
        self, *, operation_id: OperationId, governor_type: str, promotion_type: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="promote_governor", arguments={"governor_type": governor_type, "promotion_type": promotion_type}, lua_code=build_promote_governor(governor_type, promotion_type), readback=readback)

    def promote_unit(
        self, *, operation_id: OperationId, unit_index: int, promotion_type: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="promote_unit", arguments={"unit_index": unit_index, "promotion_type": promotion_type}, lua_code=build_promote_unit(unit_index, promotion_type), readback=readback, context="gamecore")

    def upgrade_unit(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        """Upgrade through the game command; never trust its acknowledgement alone."""
        return self._readback_action(
            operation_id=operation_id,
            tool="upgrade_unit",
            arguments={"unit_index": unit_index},
            lua_code=build_upgrade_unit(unit_index),
            readback=readback,
        )

    def send_envoy(
        self, *, operation_id: OperationId, city_state_player_id: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="send_envoy", arguments={"city_state_player_id": city_state_player_id}, lua_code=build_send_envoy(city_state_player_id), readback=readback)

    def choose_dedication(
        self, *, operation_id: OperationId, dedication_index: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="choose_dedication", arguments={"dedication_index": dedication_index}, lua_code=build_choose_dedication(dedication_index), readback=readback)

    def choose_pantheon(
        self, *, operation_id: OperationId, belief_type: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="choose_pantheon", arguments={"belief_type": belief_type}, lua_code=build_choose_pantheon(belief_type), readback=readback)

    def found_religion(
        self, *, operation_id: OperationId, religion_type: str, follower_belief: str, founder_belief: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="found_religion", arguments={"religion_type": religion_type, "follower_belief": follower_belief, "founder_belief": founder_belief}, lua_code=build_found_religion(religion_type, follower_belief, founder_belief), readback=readback)

    def spread_religion(
        self, *, operation_id: OperationId, unit_index: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="spread_religion", arguments={"unit_index": unit_index}, lua_code=build_spread_religion(unit_index), readback=readback)

    def recruit_great_person(
        self, *, operation_id: OperationId, individual_id: int, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(operation_id=operation_id, tool="recruit_great_person", arguments={"individual_id": individual_id}, lua_code=build_recruit_great_person(individual_id), readback=readback)

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
