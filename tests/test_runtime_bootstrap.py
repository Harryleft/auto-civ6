"""The Runtime bootstrap binds an explicit branch without legacy services."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.runtime.bootstrap import assemble_runtime
from civ_mcp.runtime.contracts import GameIdentity, OperationId, OutcomeState, SendState
from civ_mcp.runtime.session import SessionIdentityMismatchError
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.transport import TransportReceipt
from civ_mcp.runtime.turn import TurnOutcome


class _Adapter:
    def __init__(self, identities: list[GameIdentity]) -> None:
        self._identities = iter(identities)

    async def read_game_identity(self):
        return SimpleNamespace(value=next(self._identities))

    async def read_overview(self):
        return SimpleNamespace(value=SimpleNamespace(turn=42))


def test_bootstrap_binds_adapter_to_an_explicit_game_scoped_branch(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("civilization_france_42")
        assembly = await assemble_runtime(
            _Adapter([game, game]),
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        assert assembly.binding.game_id == game
        assert assembly.binding.branch_id.value == "civilization_france_42:save-0001"
        assert assembly.session.binding == assembly.binding
        assert assembly.mutations._adapter is assembly.adapter

    asyncio.run(run())


def test_bootstrap_refuses_identity_change_between_discovery_and_bind(tmp_path) -> None:
    async def run() -> None:
        with pytest.raises(SessionIdentityMismatchError):
            await assemble_runtime(
                _Adapter([GameIdentity("game-a"), GameIdentity("game-b")]),
                OperationStore(tmp_path / "operations.sqlite3"),
                branch_token="save-0001",
            )

    asyncio.run(run())


def test_bootstrap_requires_a_host_provided_branch_token(tmp_path) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="branch_token"):
            await assemble_runtime(
                _Adapter([GameIdentity("game-a")]),
                OperationStore(tmp_path / "operations.sqlite3"),
                branch_token=" ",
            )

    asyncio.run(run())


def test_bootstrap_wires_end_turn_waiting_to_a_fresh_turn_observation(tmp_path) -> None:
    class Adapter:
        def __init__(self) -> None:
            self.game = GameIdentity("civilization_france_42")
            self.overview_calls = 0
            self.submit_calls = 0

        async def read_game_identity(self):
            return SimpleNamespace(value=self.game)

        async def read_overview(self):
            self.overview_calls += 1
            turn = 10 if self.overview_calls <= 2 else 11
            return SimpleNamespace(value=SimpleNamespace(turn=turn))

        async def submit(self, _request):
            self.submit_calls += 1
            return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

    async def run() -> None:
        adapter = Adapter()
        assembly = await assemble_runtime(
            adapter,
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        async def no_immediate_evidence():
            return None

        result = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId("end-turn-10"),
                readback=no_immediate_evidence,
            ),
            decision_turn=10,
        )

        assert result.outcome is TurnOutcome.ADVANCED
        assert result.operation.outcome_state is OutcomeState.CONFIRMED
        assert adapter.submit_calls == 1

    asyncio.run(run())


def test_bootstrap_resumes_a_diplomacy_interrupt_without_sending_a_second_end_turn(tmp_path) -> None:
    initial_session = SimpleNamespace(
        session_id=9,
        other_player_id=2,
        other_civ_name="Germany",
        other_leader_name="Frederick",
        dialogue_text="Greetings",
        reason_text="First meeting",
        buttons="Accept;Reject",
        deal_summary="",
        is_at_war=False,
    )
    advanced_session = SimpleNamespace(
        session_id=9,
        other_player_id=2,
        other_civ_name="Germany",
        other_leader_name="Frederick",
        dialogue_text="Our meeting continues",
        reason_text="First meeting",
        buttons="Accept;Reject",
        deal_summary="",
        is_at_war=False,
    )

    class Adapter:
        def __init__(self) -> None:
            self.game = GameIdentity("civilization_france_42")
            self.overview_calls = 0
            self.session_calls = 0
            self.city_state_calls = 0
            self.submissions: list[str] = []

        async def read_game_identity(self):
            return SimpleNamespace(value=self.game)

        async def read_overview(self):
            self.overview_calls += 1
            turn = 11 if self.overview_calls >= 6 else 10
            return SimpleNamespace(value=SimpleNamespace(turn=turn))

        async def read_diplomacy_sessions(self, *, observed_turn):
            self.session_calls += 1
            session = initial_session if self.session_calls < 3 else advanced_session
            return SimpleNamespace(value=[session], observed_turn=observed_turn)

        async def read_city_states(self, *, observed_turn):
            self.city_state_calls += 1
            return SimpleNamespace(
                value=SimpleNamespace(
                    tokens_available=1,
                    city_states=[
                        SimpleNamespace(
                            player_id=3,
                            name="Auckland",
                            city_state_type="Trade",
                            envoys_sent=0,
                            can_send_envoy=True,
                            suzerain_id=-1,
                            leading_envoys=0,
                        )
                    ],
                ),
                observed_turn=observed_turn,
            )

        async def submit(self, request):
            self.submissions.append(request.tool)
            return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

    async def run() -> None:
        adapter = Adapter()
        assembly = await assemble_runtime(
            adapter,
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        async def no_immediate_evidence():
            return None

        interrupted = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId("end-turn-diplomacy"),
                readback=no_immediate_evidence,
            ),
            decision_turn=10,
        )
        resumed = await assembly.surface.resume_turn_decision(
            OperationId("end-turn-diplomacy"), "POSITIVE"
        )

        assert interrupted.outcome is TurnOutcome.NEEDS_DECISION
        assert interrupted.decision.decision_type == "DIPLOMACY"
        assert resumed.outcome is TurnOutcome.ADVANCED
        assert resumed.operation.outcome_state is OutcomeState.CONFIRMED
        assert adapter.submissions == ["end_turn", "respond_to_diplomacy"]
        assert adapter.city_state_calls == 0

    asyncio.run(run())


def test_bootstrap_resumes_a_city_capture_interrupt_without_a_second_end_turn(tmp_path) -> None:
    pending_capture = SimpleNamespace(
        city_id=9,
        name="Berlin",
        x=4,
        y=5,
        population=7,
        source="captured",
        owner_id=0,
        original_owner_id=2,
        previous_owner_id=3,
        allowed_choices=("KEEP", "RAZE"),
    )

    class Adapter:
        def __init__(self) -> None:
            self.game = GameIdentity("civilization_france_42")
            self.overview_calls = 0
            self.capture_calls = 0
            self.submissions: list[str] = []

        async def read_game_identity(self):
            return SimpleNamespace(value=self.game)

        async def read_overview(self):
            self.overview_calls += 1
            turn = 11 if self.overview_calls >= 6 else 10
            return SimpleNamespace(value=SimpleNamespace(turn=turn))

        async def read_pending_city_capture(self, *, observed_turn):
            self.capture_calls += 1
            return SimpleNamespace(
                value=pending_capture if self.capture_calls < 3 else None,
                observed_turn=observed_turn,
            )

        async def read_city_capture_state(self, *, x, y, observed_turn):
            assert (x, y) == (4, 5)
            return SimpleNamespace(
                value=SimpleNamespace(city_id=9, owner_id=0),
                observed_turn=observed_turn,
            )

        async def submit(self, request):
            self.submissions.append(request.tool)
            return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

    async def run() -> None:
        adapter = Adapter()
        assembly = await assemble_runtime(
            adapter,
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        async def no_immediate_evidence():
            return None

        interrupted = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId("end-turn-city-capture"),
                readback=no_immediate_evidence,
            ),
            decision_turn=10,
        )
        resumed = await assembly.surface.resume_turn_decision(
            OperationId("end-turn-city-capture"), "KEEP"
        )

        assert interrupted.outcome is TurnOutcome.NEEDS_DECISION
        assert interrupted.decision.decision_type == "CITY_CAPTURE"
        assert interrupted.decision.allowed_choices == ("KEEP", "RAZE")
        assert resumed.outcome is TurnOutcome.ADVANCED
        assert resumed.operation.outcome_state is OutcomeState.CONFIRMED
        assert adapter.submissions == ["end_turn", "resolve_city_capture"]

    asyncio.run(run())


def test_bootstrap_resumes_an_envoy_interrupt_without_a_second_end_turn(tmp_path) -> None:
    before = SimpleNamespace(
        tokens_available=1,
        city_states=[
            SimpleNamespace(
                player_id=3,
                name="Auckland",
                city_state_type="Trade",
                envoys_sent=0,
                can_send_envoy=True,
                suzerain_id=-1,
                leading_envoys=0,
            )
        ],
    )
    after = SimpleNamespace(
        tokens_available=0,
        city_states=[
            SimpleNamespace(
                player_id=3,
                name="Auckland",
                city_state_type="Trade",
                envoys_sent=1,
                can_send_envoy=False,
                suzerain_id=-1,
                leading_envoys=1,
            )
        ],
    )

    class Adapter:
        def __init__(self) -> None:
            self.game = GameIdentity("civilization_france_42")
            self.overview_calls = 0
            self.city_state_calls = 0
            self.submissions: list[str] = []

        async def read_game_identity(self):
            return SimpleNamespace(value=self.game)

        async def read_overview(self):
            self.overview_calls += 1
            turn = 11 if self.overview_calls >= 6 else 10
            return SimpleNamespace(value=SimpleNamespace(turn=turn))

        async def read_city_states(self, *, observed_turn):
            self.city_state_calls += 1
            return SimpleNamespace(
                value=before if self.city_state_calls < 3 else after,
                observed_turn=observed_turn,
            )

        async def read_diplomacy_sessions(self, *, observed_turn):
            return SimpleNamespace(value=[], observed_turn=observed_turn)

        async def submit(self, request):
            self.submissions.append(request.tool)
            return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

    async def run() -> None:
        adapter = Adapter()
        assembly = await assemble_runtime(
            adapter,
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        async def no_immediate_evidence():
            return None

        interrupted = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId("end-turn-envoy"),
                readback=no_immediate_evidence,
            ),
            decision_turn=10,
        )
        resumed = await assembly.surface.resume_turn_decision(
            OperationId("end-turn-envoy"), "3"
        )

        assert interrupted.outcome is TurnOutcome.NEEDS_DECISION
        assert interrupted.decision.decision_type == "ENVOY"
        assert interrupted.decision.allowed_choices == ("3",)
        assert interrupted.decision.facts["tokens_available"] == 1
        assert resumed.outcome is TurnOutcome.ADVANCED
        assert resumed.operation.outcome_state is OutcomeState.CONFIRMED
        assert adapter.submissions == ["end_turn", "send_envoy"]

    asyncio.run(run())
