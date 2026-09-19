"""Assemble the new Runtime Core around an already-connected Civ adapter.

This is an outer composition layer, not an MCP tool module.  It has one
purpose: bind the current game to an explicit branch token and construct the
objects that own execution, context, and domain mutation construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.civ.adapter import CivAdapter
from civ_mcp.civ.mutations import CivMutationFactory
from civ_mcp.runtime.context import ContextBuilder
from civ_mcp.runtime.contracts import BranchIdentity, Evidence, OperationId, OperationRecord, OutcomeState
from civ_mcp.runtime.mcp_surface import RuntimeMcpSurface
from civ_mcp.runtime.session import SessionBinding, SessionKernel
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.turn import DecisionInterrupt, TurnLoop, TurnObservation, TurnOutcome, TurnResult


@dataclass(frozen=True, slots=True)
class RuntimeAssembly:
    """The bound Runtime Core components for exactly one game branch."""

    binding: SessionBinding
    adapter: CivAdapter
    store: OperationStore
    session: SessionKernel
    context: ContextBuilder
    mutations: CivMutationFactory
    turn_loop: TurnLoop
    surface: RuntimeMcpSurface


async def assemble_runtime(
    adapter: CivAdapter,
    store: OperationStore,
    *,
    branch_token: str,
) -> RuntimeAssembly:
    """Bind one explicit game branch without starting legacy services.

    ``branch_token`` must be provided by the host from its stable save/load
    boundary.  The Runtime never guesses that a newly loaded timeline belongs
    to a previous branch.
    """
    if not branch_token.strip():
        raise ValueError("branch_token 不能为空；host 必须显式标识当前存档分支。")

    async def identity_probe():
        return (await adapter.read_game_identity()).value

    async def turn_probe() -> int:
        return (await adapter.read_overview()).value.turn

    session = SessionKernel(
        adapter,
        store,
        identity_probe=identity_probe,
        turn_probe=turn_probe,
    )
    game_id = await identity_probe()
    branch_id = BranchIdentity(game_id, f"{game_id.value}:{branch_token}")
    binding = await session.bind(game_id, branch_id)
    mutations = CivMutationFactory(adapter)
    turn_loop: TurnLoop

    async def continue_diplomacy(
        operation: OperationRecord, other_player_id: int, choice: str
    ) -> TurnResult:
        """Execute one model-selected response, then continue the original turn."""
        response = await session.execute(
            mutations.respond_to_diplomacy(
                operation_id=OperationId.new(),
                other_player_id=other_player_id,
                response=choice,
                observed_turn=operation.decision_turn,
            ),
            decision_turn=operation.decision_turn,
        )
        if response.outcome_state is not OutcomeState.CONFIRMED:
            return TurnResult(
                TurnOutcome.RECOVERY_REQUIRED,
                response,
                "外交响应未由新会话事实确认；不得重发，需重新读取或恢复。",
            )
        return await turn_loop.wait_for_turn(operation)

    async def continue_trade_offer(
        operation: OperationRecord, other_player_id: int, choice: str
    ) -> TurnResult:
        """Resolve one model-selected pending deal, then await the original turn."""
        response = await session.execute(
            mutations.respond_to_trade_offer(
                operation_id=OperationId.new(),
                other_player_id=other_player_id,
                choice=choice,
                observed_turn=operation.decision_turn,
            ),
            decision_turn=operation.decision_turn,
        )
        if response.outcome_state is not OutcomeState.CONFIRMED:
            return TurnResult(
                TurnOutcome.RECOVERY_REQUIRED,
                response,
                "交易回应未由待决交易状态确认；不得重发，需重新读取或恢复。",
            )
        return await turn_loop.wait_for_turn(operation)

    async def continue_city_capture(
        operation: OperationRecord, city_id: int, choice: str
    ) -> TurnResult:
        """Resolve one model-selected occupation choice, then await the same turn."""
        response = await session.execute(
            mutations.resolve_city_capture(
                operation_id=OperationId.new(),
                city_id=city_id,
                choice=choice,
                observed_turn=operation.decision_turn,
            ),
            decision_turn=operation.decision_turn,
        )
        if response.outcome_state is not OutcomeState.CONFIRMED:
            return TurnResult(
                TurnOutcome.RECOVERY_REQUIRED,
                response,
                "城市占领选择未由新游戏事实确认；不得重发，需重新读取或恢复。",
            )
        return await turn_loop.wait_for_turn(operation)

    async def continue_envoy(
        operation: OperationRecord, city_state_player_id: int
    ) -> TurnResult:
        """Send one model-selected envoy, then await the original turn only."""
        response = await session.execute(
            mutations.send_envoy(
                operation_id=OperationId.new(),
                city_state_player_id=city_state_player_id,
                observed_turn=operation.decision_turn,
            ),
            decision_turn=operation.decision_turn,
        )
        if response.outcome_state is not OutcomeState.CONFIRMED:
            return TurnResult(
                TurnOutcome.RECOVERY_REQUIRED,
                response,
                "使者派遣未由新城邦事实确认；不得重发，需重新读取或恢复。",
            )
        return await turn_loop.wait_for_turn(operation)

    async def observe_turn(operation: OperationRecord) -> TurnObservation:
        """Poll fresh game facts only; this never submits or resumes a turn."""
        try:
            identity = await adapter.read_game_identity()
        except Exception:
            return TurnObservation()
        if identity.value != binding.game_id:
            return TurnObservation(identity_changed=True)
        try:
            overview = await adapter.read_overview()
        except Exception:
            return TurnObservation()
        if overview.value.turn > operation.decision_turn:
            return TurnObservation(
                evidence=Evidence(
                    source="read_overview",
                    observed_turn=overview.value.turn,
                    detail=f"turn {operation.decision_turn} -> {overview.value.turn}",
                )
            )
        try:
            capture = await adapter.read_pending_city_capture(
                observed_turn=overview.value.turn
            )
        except Exception:
            capture = None
        if (
            capture is not None
            and capture.value is not None
            and capture.value.allowed_choices
        ):
            active_capture = capture.value
            interrupt = DecisionInterrupt(
                decision_type="CITY_CAPTURE",
                facts={
                    "city_id": active_capture.city_id,
                    "city_name": active_capture.name,
                    "x": active_capture.x,
                    "y": active_capture.y,
                    "population": active_capture.population,
                    "source": active_capture.source,
                    "owner_id": active_capture.owner_id,
                    "original_owner_id": active_capture.original_owner_id,
                    "previous_owner_id": active_capture.previous_owner_id,
                },
                allowed_choices=active_capture.allowed_choices,
                continuation_operation_id=operation.operation_id,
            )

            async def capture_continuation(choice: str) -> TurnResult:
                return await continue_city_capture(operation, active_capture.city_id, choice)

            return TurnObservation(interrupt=interrupt, continuation=capture_continuation)
        try:
            sessions = await adapter.read_diplomacy_sessions(
                observed_turn=overview.value.turn
            )
        except Exception:
            return TurnObservation()
        if len(sessions.value) > 1:
            # A model must not have a session silently selected for it.  The
            # multi-session arbitrator remains outside this Runtime surface.
            return TurnObservation()
        if sessions.value:
            active = sessions.value[0]
            try:
                pending_deals = await adapter.read_pending_deals(
                    observed_turn=overview.value.turn
                )
            except Exception:
                pending_deals = None
            if pending_deals is not None:
                matching_deals = [
                    deal
                    for deal in pending_deals.value
                    if deal.other_player_id == active.other_player_id
                ]
                if matching_deals:
                    if len(matching_deals) != 1:
                        return TurnObservation()
                    deal = matching_deals[0]
                    if not deal.items_from_them and not deal.items_from_us:
                        return TurnObservation()
                    interrupt = DecisionInterrupt(
                        decision_type="TRADE_COUNTER_OFFER",
                        facts={
                            "state": "COUNTER_OFFER",
                            "session_id": active.session_id,
                            "other_player_id": deal.other_player_id,
                            "civilization": deal.other_player_name,
                            "leader": deal.other_leader_name,
                            "items_from_them": [
                                {
                                    "item_type": item.item_type,
                                    "name": item.name,
                                    "amount": item.amount,
                                    "duration": item.duration,
                                }
                                for item in deal.items_from_them
                            ],
                            "items_from_us": [
                                {
                                    "item_type": item.item_type,
                                    "name": item.name,
                                    "amount": item.amount,
                                    "duration": item.duration,
                                }
                                for item in deal.items_from_us
                            ],
                        },
                        allowed_choices=("ACCEPT", "REJECT"),
                        continuation_operation_id=operation.operation_id,
                    )

                    async def trade_continuation(choice: str) -> TurnResult:
                        return await continue_trade_offer(
                            operation, deal.other_player_id, choice
                        )

                    return TurnObservation(
                        interrupt=interrupt, continuation=trade_continuation
                    )
            allowed_choices = (
                ("EXIT",)
                if "GOODBYE" in active.buttons.split(";")
                else ("POSITIVE", "NEGATIVE")
            )
            interrupt = DecisionInterrupt(
                decision_type="DIPLOMACY",
                facts={
                    "session_id": active.session_id,
                    "other_player_id": active.other_player_id,
                    "civilization": active.other_civ_name,
                    "leader": active.other_leader_name,
                    "dialogue": active.dialogue_text,
                    "reason": active.reason_text,
                    "visible_buttons": active.buttons,
                    "deal_summary": active.deal_summary,
                    "is_at_war": active.is_at_war,
                },
                allowed_choices=allowed_choices,
                continuation_operation_id=operation.operation_id,
            )

            async def continuation(choice: str) -> TurnResult:
                return await continue_diplomacy(operation, active.other_player_id, choice)

            return TurnObservation(interrupt=interrupt, continuation=continuation)
        try:
            envoy_status = await adapter.read_city_states(
                observed_turn=overview.value.turn
            )
        except Exception:
            return TurnObservation()
        if envoy_status.value.tokens_available <= 0:
            return TurnObservation()
        eligible_city_states = tuple(
            city_state
            for city_state in envoy_status.value.city_states
            if city_state.can_send_envoy
        )
        if not eligible_city_states:
            return TurnObservation()
        choices = tuple(str(city_state.player_id) for city_state in eligible_city_states)
        interrupt = DecisionInterrupt(
            decision_type="ENVOY",
            facts={
                "tokens_available": envoy_status.value.tokens_available,
                "city_states": [
                    {
                        "player_id": city_state.player_id,
                        "name": city_state.name,
                        "city_state_type": city_state.city_state_type,
                        "envoys_sent": city_state.envoys_sent,
                        "suzerain_id": city_state.suzerain_id,
                        "leading_envoys": city_state.leading_envoys,
                    }
                    for city_state in eligible_city_states
                ],
            },
            allowed_choices=choices,
            continuation_operation_id=operation.operation_id,
        )

        async def envoy_continuation(choice: str) -> TurnResult:
            return await continue_envoy(operation, int(choice))

        return TurnObservation(interrupt=interrupt, continuation=envoy_continuation)

    context = ContextBuilder(adapter, session)
    turn_loop = TurnLoop(session, observer=observe_turn)
    return RuntimeAssembly(
        binding=binding,
        adapter=adapter,
        store=store,
        session=session,
        context=context,
        mutations=mutations,
        turn_loop=turn_loop,
        surface=RuntimeMcpSurface(
            context=context,
            session=session,
            turn_loop=turn_loop,
        ),
    )
