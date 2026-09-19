"""A narrow Civ6 adapter: typed reads and one-shot mutation submission only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from civ_mcp.lua.cities import (
    build_cities_query,
    build_city_capture_state_query,
    build_city_attack_target_query,
    build_district_placement_query,
    build_city_production_query,
    build_city_purchase_query,
    build_pending_city_capture_query,
    parse_cities_response,
    parse_city_attack_target_response,
    parse_city_capture_state_response,
    parse_district_placement_response,
    parse_city_production_response,
    parse_city_purchase_response,
    parse_pending_city_capture_response,
)
from civ_mcp.lua.diplomacy import (
    build_diplomacy_query,
    build_diplomacy_session_query,
    parse_diplomacy_response,
    parse_diplomacy_sessions,
)
from civ_mcp.lua.economy import (
    build_trade_destinations_query,
    build_trade_routes_query,
    parse_trade_destinations_response,
    parse_trade_routes_response,
)
from civ_mcp.lua.governance import (
    build_city_states_query,
    build_available_governments_query,
    build_dedications_query,
    build_governors_query,
    build_policies_query,
    build_unit_promotions_query,
    parse_city_states_response,
    parse_available_governments_response,
    parse_dedications_response,
    parse_governors_response,
    parse_policies_response,
    parse_unit_promotions_response,
)
from civ_mcp.lua.great_people import build_great_people_query, parse_great_people_response
from civ_mcp.lua.models import (
    CityCaptureState,
    CityInfo,
    CivInfo,
    CombatTarget,
    DistrictPlacementCandidate,
    DiplomacySession,
    DedicationStatus,
    EnvoyStatus,
    GameOverview,
    GreatPersonInfo,
    GovernmentChoice,
    GovernmentStatus,
    GovernorStatus,
    PantheonStatus,
    PendingCityCapture,
    ProductionOption,
    PurchaseOption,
    TechCivicStatus,
    TradeDestination,
    TradeRouteStatus,
    UnitInfo,
    UnitPromotionStatus,
    VictoryProgress,
)
from civ_mcp.lua.overview import (
    build_game_identity_query,
    build_overview_query,
    parse_game_identity_response,
    parse_overview_response,
)
from civ_mcp.lua.religion import build_pantheon_status_query, parse_pantheon_status_response
from civ_mcp.lua.tech import build_tech_civics_query, parse_tech_civics_response
from civ_mcp.lua.units import (
    build_attack_followup_query,
    build_attack_target_query,
    build_units_query,
    parse_attack_target_response,
    parse_combat_targets_response,
    parse_units_response,
)
from civ_mcp.lua.victory import build_victory_progress_query, parse_victory_progress_response
from civ_mcp.runtime.contracts import GameIdentity
from civ_mcp.runtime.transport import FireTunerTransport, Frame, TransportReceipt


SENTINEL = "---END---"
T = TypeVar("T")
StateResolver = Callable[[str], int]


class CivReadError(RuntimeError):
    """A Civ6 read failed or was incomplete; it is never an empty result."""


@dataclass(frozen=True, slots=True)
class CivReadRequest(Generic[T]):
    """A domain-owned Lua query and its typed decoder."""

    tool: str
    lua_code: str
    decode: Callable[[tuple[str, ...]], T]
    coverage: str
    context: str = "gamecore"


@dataclass(frozen=True, slots=True)
class CivReadResult(Generic[T]):
    """A typed game fact with its direct source and observation boundary."""

    value: T
    source: str
    observed_turn: int | None
    coverage: str


@dataclass(frozen=True, slots=True)
class CivMutationRequest:
    """A requested player action, without strategy or recovery behavior."""

    tool: str
    lua_code: str
    context: str = "ingame"


class CivAdapter:
    """Translate Civ6 Lua calls to typed reads and transport receipts.

    It owns neither operation state nor game-process recovery.  SessionKernel
    will own the former; RecoverySupervisor will own the latter.
    """

    def __init__(
        self,
        transport: FireTunerTransport,
        *,
        gamecore_state: int | None = None,
        ingame_state: int | None = None,
        state_resolver: StateResolver | None = None,
    ) -> None:
        self._transport = transport
        if state_resolver is not None and (
            gamecore_state is not None or ingame_state is not None
        ):
            raise ValueError("state_resolver 与固定 Lua state 不能同时提供。")
        if state_resolver is None and (
            gamecore_state is None or ingame_state is None
        ):
            raise ValueError("CivAdapter 需要 Lua state 或 state_resolver。")
        self._gamecore_state = gamecore_state
        self._ingame_state = ingame_state
        self._state_resolver = state_resolver

    async def read(self, request: CivReadRequest[T], *, observed_turn: int) -> CivReadResult[T]:
        state = self._state_for(request.context)
        receipt = await self._transport.execute_read(
            self._command(state, request.lua_code),
            is_complete=_is_sentinel,
        )
        if not receipt.complete:
            raise CivReadError(f"{request.tool} 未得到完整游戏响应：{receipt.error!r}")
        lines = tuple(
            value for frame in receipt.frames
            if (value := _output_value(frame)) is not None and value != SENTINEL
        )
        return CivReadResult(
            value=request.decode(lines),
            source="civ6:FireTuner",
            observed_turn=observed_turn,
            coverage=request.coverage,
        )

    async def read_overview(self) -> CivReadResult[GameOverview]:
        """Read the current game identity and turn without starting GameState."""
        receipt = await self._read_receipt(
            "get_game_overview", build_overview_query(), context="gamecore"
        )
        overview = parse_overview_response(_receipt_lines(receipt))
        return CivReadResult(
            value=overview,
            source="civ6:FireTuner",
            observed_turn=overview.turn,
            coverage="CURRENT_GAME:COMPLETE",
        )

    async def read_game_identity(self) -> CivReadResult[GameIdentity]:
        """Read a stable game identity without instantiating legacy GameState."""
        receipt = await self._read_receipt(
            "get_game_identity", build_game_identity_query(), context="gamecore"
        )
        civilization, seed = parse_game_identity_response(_receipt_lines(receipt))
        return CivReadResult(
            value=GameIdentity(f"{civilization}_{seed}"),
            source="civ6:FireTuner",
            observed_turn=None,
            coverage="CURRENT_GAME:COMPLETE",
        )

    async def read_cities(self, *, observed_turn: int) -> CivReadResult[list[CityInfo]]:
        result = await self.read(
            CivReadRequest(
                tool="get_cities",
                lua_code=build_cities_query(),
                decode=lambda lines: parse_cities_response(list(lines))[0],
                coverage="OWN_CITIES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )
        return result

    async def read_city_purchases(
        self, *, city_id: int, yield_type: str, observed_turn: int
    ) -> CivReadResult[list[PurchaseOption]]:
        """Read only immediate purchases the game currently accepts."""
        normalized_yield = yield_type.upper()
        return await self.read(
            CivReadRequest(
                tool="get_city_purchases",
                lua_code=build_city_purchase_query(city_id, normalized_yield),
                decode=lambda lines: parse_city_purchase_response(
                    list(lines), yield_type=normalized_yield
                ),
                coverage="CITY_PURCHASE_CANDIDATES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_city_production(
        self, *, city_id: int, observed_turn: int
    ) -> CivReadResult[list[ProductionOption]]:
        """Read the exact non-placement items the city may begin building."""
        return await self.read(
            CivReadRequest(
                tool="get_city_production",
                lua_code=build_city_production_query(city_id),
                decode=_decode_city_production,
                coverage="CITY_PRODUCTION_CANDIDATES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_trade_destinations(
        self, *, unit_index: int, observed_turn: int
    ) -> CivReadResult[list[TradeDestination]]:
        """Read only destinations the live trade-route operation accepts."""
        return await self.read(
            CivReadRequest(
                tool="get_trade_destinations",
                lua_code=build_trade_destinations_query(unit_index),
                decode=_decode_trade_destinations,
                coverage="TRADE_ROUTE_DESTINATIONS:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_trade_routes(
        self, *, observed_turn: int
    ) -> CivReadResult[TradeRouteStatus]:
        """Read active routes so a submitted route can be verified by identity."""
        return await self.read(
            CivReadRequest(
                tool="get_trade_routes",
                lua_code=build_trade_routes_query(),
                decode=lambda lines: parse_trade_routes_response(list(lines)),
                coverage="TRADE_ROUTES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_pending_city_capture(
        self, *, observed_turn: int
    ) -> CivReadResult[PendingCityCapture | None]:
        """Read a current city occupation blocker without choosing for the model."""
        return await self.read(
            CivReadRequest(
                tool="get_pending_city_capture",
                lua_code=build_pending_city_capture_query(),
                decode=lambda lines: parse_pending_city_capture_response(list(lines)),
                coverage="PENDING_CITY_CAPTURE:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_city_capture_state(
        self, *, x: int, y: int, observed_turn: int
    ) -> CivReadResult[CityCaptureState]:
        """Read one occupied coordinate to verify an explicit decision outcome."""
        return await self.read(
            CivReadRequest(
                tool="get_city_capture_state",
                lua_code=build_city_capture_state_query(x, y),
                decode=lambda lines: parse_city_capture_state_response(list(lines)),
                coverage="CITY_CAPTURE_COORDINATE:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_units(self, *, observed_turn: int) -> CivReadResult[list[UnitInfo]]:
        return await self.read(
            CivReadRequest(
                tool="get_units",
                lua_code=build_units_query(),
                decode=lambda lines: parse_units_response(list(lines)),
                coverage="OWN_UNITS:COMPLETE;FOREIGN_UNITS:CURRENTLY_VISIBLE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_attack_target(
        self, *, unit_index: int, target_x: int, target_y: int, observed_turn: int
    ) -> CivReadResult[tuple[CombatTarget, str]]:
        """Validate one exact attack target without sending an operation."""
        return await self.read(
            CivReadRequest(
                tool="get_unit_attack_target",
                lua_code=build_attack_target_query(unit_index, target_x, target_y),
                decode=lambda lines: parse_attack_target_response(list(lines)),
                coverage="UNIT_ATTACK_TARGET:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_city_attack_target(
        self, *, city_id: int, target_x: int, target_y: int, observed_turn: int
    ) -> CivReadResult[CombatTarget]:
        """Validate one exact city attack target without sending a command."""
        return await self.read(
            CivReadRequest(
                tool="get_city_attack_target",
                lua_code=build_city_attack_target_query(city_id, target_x, target_y),
                decode=lambda lines: parse_city_attack_target_response(list(lines)),
                coverage="CITY_ATTACK_TARGET:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_district_placements(
        self, *, city_id: int, district_type: str, observed_turn: int
    ) -> CivReadResult[list[DistrictPlacementCandidate]]:
        """Read only current tiles the exact district BUILD operation allows."""
        return await self.read(
            CivReadRequest(
                tool="get_district_placements",
                lua_code=build_district_placement_query(city_id, district_type),
                decode=lambda lines: parse_district_placement_response(list(lines)),
                coverage="DISTRICT_PLACEMENT_CANDIDATES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_combat_targets(
        self, *, target_x: int, target_y: int, observed_turn: int
    ) -> CivReadResult[list[CombatTarget]]:
        """Read target-tile units by stable owner/unit IDs after combat."""
        return await self.read(
            CivReadRequest(
                tool="get_combat_targets",
                lua_code=build_attack_followup_query(target_x, target_y),
                decode=lambda lines: parse_combat_targets_response(list(lines)),
                coverage="COMBAT_TARGET_TILE:CURRENTLY_VISIBLE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_unit_promotions(
        self, *, unit_index: int, observed_turn: int
    ) -> CivReadResult[UnitPromotionStatus]:
        """Read one unit's legal and owned promotions from GameCore."""
        return await self.read(
            CivReadRequest(
                tool="get_unit_promotions",
                lua_code=build_unit_promotions_query(unit_index),
                decode=_decode_unit_promotions,
                coverage="UNIT_PROMOTIONS:COMPLETE",
                context="gamecore",
            ),
            observed_turn=observed_turn,
        )

    async def read_diplomacy(self, *, observed_turn: int) -> CivReadResult[list[CivInfo]]:
        return await self.read(
            CivReadRequest(
                tool="get_diplomacy",
                lua_code=build_diplomacy_query(),
                decode=lambda lines: parse_diplomacy_response(list(lines)),
                coverage="MET_CIVILIZATIONS:COMPLETE;UNMET_CIVILIZATIONS:UNOBSERVED",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_diplomacy_sessions(
        self, *, observed_turn: int
    ) -> CivReadResult[list[DiplomacySession]]:
        """Read active diplomacy sessions without resolving any of them."""
        return await self.read(
            CivReadRequest(
                tool="get_pending_diplomacy",
                lua_code=build_diplomacy_session_query(),
                decode=lambda lines: parse_diplomacy_sessions(list(lines)),
                coverage="OPEN_DIPLOMACY_SESSIONS:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_tech_civics(
        self, *, observed_turn: int
    ) -> CivReadResult[TechCivicStatus]:
        """Read active research/civic selections and legal choices as game facts."""
        return await self.read(
            CivReadRequest(
                tool="get_tech_civics",
                lua_code=build_tech_civics_query(),
                decode=lambda lines: parse_tech_civics_response(list(lines)),
                coverage="RESEARCH_AND_CIVICS:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_pantheon_status(
        self, *, observed_turn: int
    ) -> CivReadResult[PantheonStatus]:
        """Read the current pantheon and game-legal beliefs as a typed fact."""
        return await self.read(
            CivReadRequest(
                tool="get_pantheon_status",
                lua_code=build_pantheon_status_query(),
                decode=lambda lines: parse_pantheon_status_response(list(lines)),
                coverage="PANTHEON:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_dedications(
        self, *, observed_turn: int
    ) -> CivReadResult[DedicationStatus]:
        """Read current era choices and active commemorations as game facts."""
        return await self.read(
            CivReadRequest(
                tool="get_dedications",
                lua_code=build_dedications_query(),
                decode=lambda lines: parse_dedications_response(list(lines)),
                coverage="DEDICATIONS:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_city_states(
        self, *, observed_turn: int
    ) -> CivReadResult[EnvoyStatus]:
        """Read local envoy tokens and every met city-state's send eligibility."""
        return await self.read(
            CivReadRequest(
                tool="get_city_states",
                lua_code=build_city_states_query(),
                decode=_decode_city_states,
                coverage=(
                    "ENVOY_TOKENS:COMPLETE;MET_CITY_STATES:COMPLETE;"
                    "UNMET_CITY_STATES:UNOBSERVED"
                ),
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_governors(
        self, *, observed_turn: int
    ) -> CivReadResult[GovernorStatus]:
        """Read appointment, placement, and promotion eligibility as game facts."""
        return await self.read(
            CivReadRequest(
                tool="get_governors",
                lua_code=build_governors_query(),
                decode=_decode_governors,
                coverage="GOVERNORS:COMPLETE;RULESET:EXPANSION_ONLY",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_governments(
        self, *, observed_turn: int
    ) -> CivReadResult[list[GovernmentChoice]]:
        """Read every unlocked government and mark the exact active choice."""
        return await self.read(
            CivReadRequest(
                tool="get_governments",
                lua_code=build_available_governments_query(),
                decode=_decode_governments,
                coverage="UNLOCKED_GOVERNMENTS:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_policies(
        self, *, observed_turn: int
    ) -> CivReadResult[GovernmentStatus]:
        """Read current policy slots and their current legal replacement candidates."""
        return await self.read(
            CivReadRequest(
                tool="get_policies",
                lua_code=build_policies_query(),
                decode=_decode_policies,
                coverage="POLICY_SLOTS:COMPLETE;POLICY_CANDIDATES:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_great_people(
        self, *, observed_turn: int
    ) -> CivReadResult[list[GreatPersonInfo]]:
        """Read the recruit pool and local-player claims without strategy logic."""
        return await self.read(
            CivReadRequest(
                tool="get_great_people",
                lua_code=build_great_people_query(),
                decode=_decode_great_people,
                coverage="GREAT_PERSON_TIMELINE:COMPLETE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def read_victory_progress(
        self, *, observed_turn: int
    ) -> CivReadResult[VictoryProgress]:
        return await self.read(
            CivReadRequest(
                tool="get_victory_progress",
                lua_code=build_victory_progress_query(),
                decode=lambda lines: parse_victory_progress_response(list(lines)),
                coverage="MET_CIVILIZATIONS:CURRENTLY_VISIBLE",
                context="ingame",
            ),
            observed_turn=observed_turn,
        )

    async def submit(self, request: CivMutationRequest) -> TransportReceipt:
        state = self._state_for(request.context)
        return await self._transport.execute_mutation(
            self._command(state, request.lua_code), is_complete=_is_sentinel
        )

    def _state_for(self, context: str) -> int:
        if context not in {"gamecore", "ingame"}:
            raise ValueError("Civ Lua context 必须是 gamecore 或 ingame。")
        if self._state_resolver is not None:
            return self._state_resolver(context)
        return self._gamecore_state if context == "gamecore" else self._ingame_state

    @staticmethod
    def _command(state: int, lua_code: str) -> str:
        return f"CMD:{state}:{lua_code}"

    async def _read_receipt(
        self, tool: str, lua_code: str, *, context: str
    ) -> TransportReceipt:
        state = self._state_for(context)
        receipt = await self._transport.execute_read(
            self._command(state, lua_code), is_complete=_is_sentinel
        )
        if not receipt.complete:
            raise CivReadError(f"{tool} 未得到完整游戏响应：{receipt.error!r}")
        return receipt


def _is_sentinel(frame: Frame) -> bool:
    return _output_value(frame) == SENTINEL


def _output_value(frame: Frame) -> str | None:
    if not frame.payload.startswith("O"):
        return None
    separator = frame.payload.find(": ", 2)
    return frame.payload[separator + 2 :] if separator >= 0 else frame.payload.lstrip("O\x00").strip()


def _receipt_lines(receipt: TransportReceipt) -> list[str]:
    return [
        value for frame in receipt.frames
        if (value := _output_value(frame)) is not None and value != SENTINEL
    ]


def _decode_great_people(lines: tuple[str, ...]) -> list[GreatPersonInfo]:
    if not any(line.startswith("GP_STATUS|") for line in lines):
        raise ValueError("缺少 Great People GP_STATUS 响应。")
    return parse_great_people_response(list(lines))


def _decode_unit_promotions(lines: tuple[str, ...]) -> UnitPromotionStatus:
    if not any(line.startswith("UNIT|") for line in lines):
        raise ValueError("缺少 unit promotion UNIT 响应。")
    return parse_unit_promotions_response(list(lines))


def _decode_city_states(lines: tuple[str, ...]) -> EnvoyStatus:
    if not any(line.startswith("TOKENS|") for line in lines):
        raise ValueError("缺少 city-state TOKENS 响应。")
    return parse_city_states_response(list(lines))


def _decode_governors(lines: tuple[str, ...]) -> GovernorStatus:
    if error := next((line for line in lines if line.startswith("ERR:")), None):
        raise ValueError(error[4:])
    if not any(line.startswith("STATUS|") for line in lines):
        raise ValueError("缺少 governor STATUS 响应。")
    return parse_governors_response(list(lines))


def _decode_governments(lines: tuple[str, ...]) -> list[GovernmentChoice]:
    governments = parse_available_governments_response(list(lines))
    if sum(government.is_current for government in governments) != 1:
        raise ValueError("缺少唯一当前政府响应。")
    return governments


def _decode_policies(lines: tuple[str, ...]) -> GovernmentStatus:
    if not any(line.startswith("GOV|") for line in lines):
        raise ValueError("缺少 policies GOV 响应。")
    return parse_policies_response(list(lines))


def _decode_trade_destinations(lines: tuple[str, ...]) -> list[TradeDestination]:
    if error := next((line for line in lines if line.startswith("ERR:")), None):
        raise ValueError(error[4:])
    return parse_trade_destinations_response(list(lines))


def _decode_city_production(lines: tuple[str, ...]) -> list[ProductionOption]:
    if error := next((line for line in lines if line.startswith("ERR:")), None):
        raise ValueError(error[4:])
    return parse_city_production_response(list(lines))
