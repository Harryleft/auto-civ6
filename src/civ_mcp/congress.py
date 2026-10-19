"""Own the original Congress intent; native q remains the only phase protocol.

Congress may register a native event handler and explicitly advance turn segments.
That lifecycle cannot use ActionLifecycle's strict same-turn completion check.
This bridge stores only the intent/binding; it never casts or resumes a phase.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from civ_mcp.actions import _action_result
from civ_mcp.connection import LuaError
from civ_mcp.lua.congress import build_congress_receipt, build_congress_scope_guard, build_open_world_congress, build_register_wc_voter


@dataclass
class CongressRequest:
    kind: str
    token: str
    intent: str
    world: tuple[str, str, str]
    turn: int
    state: str = "UNKNOWN"
    invalidated: bool = False


class CongressProgress:
    def __init__(self):
        self.request: CongressRequest | None = None

    @property
    def pending(self) -> bool:
        return self.request is not None

    def invalidate_scope(self) -> None:
        if self.request:
            self.request.invalidated = True

    def unknown(self) -> str:
        q = self.request
        return (f"ERR:OUTCOME_UNKNOWN|Congress {q.kind if q else ''} original request remains pending; "
                f"state:{q.state if q else 'UNKNOWN'}; plan:{q.intent if q else ''}. "
                "Repeat the original Congress call to inspect; no native request is replayed.")

    @staticmethod
    def scope(rows):
        scopes = [row.split('|') for row in rows if row.startswith('WC_SCOPE|')]
        if len(scopes) != 1 or len(scopes[0]) != 6:
            raise ValueError('Congress world scope unavailable')
        _, owner, civ, seed, turn, stage = scopes[0]
        int(owner); int(seed)
        return (owner, civ, seed), int(turn), stage

    async def confirm(self, conn) -> str:
        q = self.request
        if q is None:
            return "NO_CHANGE|No pending Congress request."
        if q.invalidated:
            return self.unknown()
        # A failed fresh read must not authorize end_turn using old REGISTERED data.
        q.state = 'UNKNOWN'
        try:
            rows = await conn.execute_write(build_congress_receipt(q.kind, q.token))
            world, _, _ = self.scope(rows)
            receipts = [row.split('|') for row in rows if row.startswith('WC_RECEIPT|')]
            if world != q.world:
                q.invalidated = True
                return self.unknown()
            if len(receipts) != 1 or len(receipts[0]) != 6 or receipts[0][1] != q.token:
                return self.unknown()
            _, _, state, stage, turn, allocation = receipts[0]
            if int(turn) != q.turn:
                q.invalidated = True
                return self.unknown()
            q.state = state
            if state == 'STALE':
                q.invalidated = True
            elif state in ('OPENED', 'FINISHED', 'FAILED'):
                self.request = None
                if state == 'OPENED':
                    return f"WC_VOTING_ALREADY_OPEN|stage:{stage}|turn:{turn}"
                if state == 'FAILED':
                    if q.kind == 'open':
                        return 'ERR:WC_OPEN_REFUSED|Original start failed before entry; no start event submitted.'
                    return 'ERR:WC_VOTE_PREFERENCES|Original plan failed before any vote; no votes submitted.'
                return f"WC_STAGE_FINISHED|stage:{stage}|requested allocation:{allocation}|Native stage advanced; voting outcomes require review."
            return f"{self.unknown()} WC_REQUEST|{state}|stage:{stage}|allocation:{allocation}"
        except (OSError, TimeoutError, LuaError, ValueError):
            return self.unknown()

    async def submit(self, conn, kind: str, votes=None, *, allows_world=None) -> str:
        token = uuid.uuid4().hex
        # Validate the full public input even if an earlier plan is pending.
        code = build_open_world_congress(token=token) if kind == 'open' else build_register_wc_voter(votes, token=token)
        intent = 'open' if kind == 'open' else json.dumps(votes, sort_keys=True, separators=(',', ':'))
        if self.request:
            same = self.request.kind == kind and self.request.intent == intent
            result = await self.confirm(conn)
            # A repeat never confirms an old operation and submits again in one call.
            if same or self.pending:
                return result
        try:
            world, turn, stage = self.scope(await conn.execute_write(build_congress_receipt(kind, token)))
        except (OSError, TimeoutError, LuaError, ValueError):
            return 'ERR:OUTCOME_UNKNOWN|Congress scope unavailable; no request submitted.'
        if allows_world is not None and not allows_world((world[1], int(world[2]), int(world[0]))):
            return 'ERR:OUTCOME_UNKNOWN|Congress cannot resolve a pending end-turn from another or unknown world; no request submitted.'
        guard = build_congress_scope_guard(world, turn, stage)
        self.request = CongressRequest(kind, token, intent, world, turn)
        try:
            rows = await conn.execute_write(guard + code)
        except (OSError, TimeoutError, LuaError):
            return self.unknown()
        refusals = ('ERR:WC_SCOPE_REFUSED|', 'ERR:WC_OPEN_REFUSED|', 'ERR:WC_OPEN_UNAVAILABLE|', 'ERR:WC_CHOICES_UNKNOWN|',
                    'ERR:WC_NO_CHOICES|', 'ERR:WC_PHASE_UNKNOWN|', 'ERR:WC_PAUSED|', 'ERR:WC_VOTE_PREFERENCES|')
        if any(row.startswith(refusals) for row in rows):
            self.request = None
            return _action_result(rows)
        result = await self.confirm(conn)
        if self.pending:
            # Acknowledgements remain visible as requested/registered, not completion.
            return result + '\n' + _action_result(rows)
        if kind == 'open' and any(row.startswith('OK:WC_VOTING_OPEN|') for row in rows):
            return _action_result(rows)
        return result
