"""Owned observations and one explicit native diplomacy response at a time."""
from __future__ import annotations

import asyncio
import uuid
import time
from dataclasses import dataclass

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_test_trade

from civ_mcp.connection import LuaError
from civ_mcp.lua import trade as native


class DiplomacyProgress:
    def __init__(self) -> None:
        self.nonce = uuid.uuid4().hex
        self.scope_invalid = False
        self.observer_ready = False
        self.observer_scope: str | None = None
        self.trade = None
        self.response: tuple[str, int, int, int] | None = None

    @property
    def pending(self) -> bool:
        return self.response is not None or self.trade is not None

    def invalidate_scope(self) -> None:
        if self.pending:
            self.scope_invalid = True
        else:
            self.nonce = uuid.uuid4().hex
            self.observer_ready = False
            self.observer_scope = None

    async def ensure_observer(self, conn) -> bool:
        if self.scope_invalid:
            return False
        execute = getattr(conn, "execute_in_named_state", None)
        if execute is None:
            return False
        try:
            rows = await execute("DiplomacyActionView", native.build_observer_install(self.nonce))
        except (OSError, TimeoutError, LuaError):
            return False
        scopes = [row.split("|", 1)[1] for row in rows if row.startswith("DIPLO_SCOPE|")]
        self.observer_ready = "DIPLO_OBSERVER|READY" in rows and len(scopes) == 1
        if self.observer_ready:
            self.observer_scope = scopes[0]
        return self.observer_ready

    async def choices(self, conn, sid: int, target: int) -> tuple[list[str] | None, int | None]:
        if not await self.ensure_observer(conn):
            return None, None
        try:
            rows = await conn.execute_in_named_state("DiplomacyActionView", native.build_choices_query(self.nonce, sid, target))
            headers = [row for row in rows if row.startswith("DIPLO_CHOICES|KNOWN|")]
            if len(headers) != 1:
                return None, None
            seq = int(headers[0].split("|")[2])
            return [row.split("|", 1)[1] for row in rows if row.startswith("DIPLO_CHOICE|")], seq
        except (OSError, TimeoutError, LuaError, ValueError, IndexError):
            return None, None

    async def respond(self, conn, sid: int, target: int, response: str) -> str:
        if self.scope_invalid:
            return "ERR:OUTCOME_UNKNOWN|Diplomacy belongs to a changed world; explicit recovery required."
        if self.response is not None:
            return await self.confirm_response(conn)
        keys, seq = await self.choices(conn, sid, target)
        if keys is None or seq is None:
            return "ERR:OUTCOME_UNKNOWN|Current native diplomacy choices UNKNOWN; no response submitted."
        key = "CHOICE_" + response.upper()
        if key not in keys:
            return f"ERR:NOT_AVAILABLE|{key} is not a current native choice; no response submitted. Choices: {', '.join(keys)}"
        token = uuid.uuid4().hex
        self.response = (token, sid, target, seq)
        try:
            rows = await conn.execute_in_named_state("DiplomacyActionView", native.build_native_response(self.nonce, token, sid, target, seq, key))
            if any(row.startswith("DIPLO_RESPONSE|NOT_SUBMITTED|") for row in rows):
                self.response = None
                return "ERR:NOT_AVAILABLE|Native statement changed; no response submitted."
        except (OSError, TimeoutError, LuaError):
            return "ERR:OUTCOME_UNKNOWN|Diplomacy response outcome unknown; repeat only confirms it."
        return await self.confirm_response(conn)

    async def confirm_response(self, conn) -> str:
        if self.scope_invalid:
            return "ERR:OUTCOME_UNKNOWN|Diplomacy belongs to a changed world; explicit recovery required."
        token, sid, target, seq = self.response
        try:
            rows = await conn.execute_in_named_state("DiplomacyActionView", native.build_response_query(self.nonce, token, sid, target, seq))
            if rows == ["DIPLO_RESPONSE|CLOSED"]:
                self.response = None
                return "OK:RESPONDED|SESSION_CLOSED"
            if rows == ["DIPLO_RESPONSE|CONTINUES"]:
                self.response = None
                return "OK:RESPONDED|SESSION_CONTINUES; inspect current native choices."
        except (OSError, TimeoutError, LuaError):
            pass
        return "ERR:OUTCOME_UNKNOWN|Native diplomacy response is unconfirmed; no response was resent."

    def trade_summary(self) -> str:
        if self.scope_invalid:
            return "ERR:OUTCOME_UNKNOWN|Diplomacy world changed; explicit protected recovery required."
        if self.trade is not None:
            return (f"ERR:OUTCOME_UNKNOWN|Trade attempt {self.trade.phase} with player {self.trade.target} is pending. "
                    "Repeat the original trade tool only to confirm; no RequestSession, proposal or response will be resent.")
        return "ERR:OUTCOME_UNKNOWN|Native diplomacy response is pending; repeat response only to confirm."

    async def diagnostics(self, conn) -> str:
        """Read the existing observer only; never install, advance, or retire it."""
        attempt = self.trade
        if attempt is None:
            return ""
        if self.scope_invalid or self.observer_scope is None:
            return "diplomacy_observation: world=UNKNOWN reason=SCOPE_UNCONFIRMED"
        try:
            rows = await conn.execute_in_named_state("DiplomacyActionView", native.build_trade_diagnostics(self.nonce, self.observer_scope, attempt.token, attempt.sid))
            facts = [row.split("|", 1)[1] for row in rows if row.startswith("DIPLO_DIAGNOSTIC|")]
            if facts:
                return "\n".join(facts)
        except (OSError, TimeoutError, LuaError):
            return "diplomacy_observation: UNKNOWN reason=READ_FAILED"
        return "diplomacy_observation: UNKNOWN reason=NO_DIAGNOSTIC_RECEIPT"

    async def run_trade(self, gs, target: int, offer: list[dict], request: list[dict], *, preview: bool) -> str:
        if self.scope_invalid:
            return self.trade_summary()
        if self.response is not None:
            return self.trade_summary()
        if self.trade is None and gs._diplomacy_cleanup.pending:
            cleanup = await gs._diplomacy_cleanup.close(gs.conn)
            return "No new trade submitted; confirming previous native cleanup.\n" + cleanup.message
        if self.trade is None:
            if gs.turn_pending or gs.save_pending or gs.replacement_pending or gs.pending_action_summary:
                return "ERR:OUTCOME_UNKNOWN|An action/save/load/end-turn is pending; no trade submitted."
            if not await self.ensure_observer(gs.conn):
                return "ERR:OUTCOME_UNKNOWN|Native statement observer unavailable; no trade submitted."
            attempt = TradeAttempt(uuid.uuid4().hex, target, preview, [dict(row) for row in offer], [dict(row) for row in request])
            self.trade = attempt
            try:
                rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_open(self.nonce, attempt.token, target, ai_only=not preview))
                if any(row.startswith("TRADE|NOT_SUBMITTED|") for row in rows):
                    self.trade = None
                    return "ERR:NOT_SUBMITTED|" + " ".join(rows)
            except (OSError, TimeoutError, LuaError):
                return self.trade_summary()
        return await self.resume_trade(gs)

    async def resume_trade(self, gs) -> str:
        if self.scope_invalid:
            return self.trade_summary()
        attempt = self.trade
        if attempt is None:
            return "No trade attempt pending."
        try:
            async with asyncio.timeout(TRADE_CONFIRM_SECONDS):
                deadline = time.monotonic() + TRADE_CONFIRM_SECONDS
                while time.monotonic() < deadline:
                    if attempt.phase == "OPEN_ENTERED":
                        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_session(self.nonce, attempt.token))
                        bound = [row for row in rows if row.startswith("TRADE_SESSION|BOUND|")]
                        if not bound:
                            await asyncio.sleep(TRADE_POLL_SECONDS)
                            continue
                        attempt.sid = int(bound[0].split("|")[2])
                        rows = await _named(gs.conn, "DiplomacyDealView", native.build_trade_deal_ready(self.observer_scope, attempt.target, attempt.sid))
                        if rows != ["TRADE_DEAL|READY"]:
                            await asyncio.sleep(TRADE_POLL_SECONDS)
                            continue
                        action = "EQUALIZE" if attempt.preview else "INSPECT"
                        armed = await _named(gs.conn, "DiplomacyActionView", native.build_trade_arm_action(self.nonce, attempt.token, action))
                        if armed != ["TRADE|ARMED"]:
                            return self.trade_summary()
                        attempt.phase = "TERMS_ENTERED"
                        rows = await _named(gs.conn, "DiplomacyDealView", native.build_trade_send(self.observer_scope, attempt.target, attempt.sid, attempt.token, attempt.offer, attempt.request, action))
                        if any(row.startswith("TRADE|NOT_SUBMITTED|") or row == "TRADE_DEAL|NOT_READY" for row in rows):
                            attempt.phase = "OPEN_ENTERED"  # Native final guard proved zero term submission.
                            return self.trade_summary()
                    if attempt.phase == "TERMS_ENTERED":
                        rows = await _named(gs.conn, "DiplomacyDealView", native.build_trade_deal_receipt(self.observer_scope, attempt.token))
                        originals = [row.split("|", 1)[1] for row in rows if row.startswith("TRADE_ORIGINAL|")]
                        if any(row.startswith("TRADE_PREPARE|NOT_SUBMITTED|") for row in rows):
                            attempt.result = "ERR:NOT_SUBMITTED|Native terms unavailable; no proposal or equalize was sent."
                            attempt.phase = "PREPARE_EXIT_READY"
                        else:
                            if not originals:
                                return self.trade_summary()
                            attempt.original = originals[0]
                            attempt.phase = "RESPONSE_WAIT"
                    if attempt.phase in ("RESPONSE_WAIT", "ACCEPT_WAIT"):
                        if attempt.phase == "ACCEPT_WAIT":
                            receipt = await _named(gs.conn, "DiplomacyDealView", native.build_trade_accept_receipt(self.observer_scope, attempt.target, attempt.sid, attempt.token, attempt.counter))
                            if receipt != ["TRADE_ACCEPT_RECEIPT|ENTERED"]:
                                return self.trade_summary()
                            final = await _named(gs.conn, "DiplomacyActionView", native.build_trade_human_accept(self.nonce, attempt.token))
                            if len(final) == 1 and final[0].startswith("TRADE_FINAL|HUMAN_ACCEPT_DEAL|") and final[0].split("|")[-1].isdigit():
                                attempt.result = "ACCEPTED|Native HUMAN_ACCEPT_DEAL confirms this entered acceptance."
                                attempt.phase = "DIALOGUE"
                                return attempt.result + "\nInspect get_pending_diplomacy and use its current native choices; no response was chosen automatically."
                        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_response(self.nonce, attempt.token))
                        headers = [row.split("|") for row in rows if row.startswith("TRADE_RESPONSE|KNOWN|")]
                        signatures = [row.split("|", 1)[1] for row in rows if row.startswith("TRADE_COUNTER|")]
                        if len(headers) != 1 or len(signatures) != 1:
                            await asyncio.sleep(TRADE_POLL_SECONDS)
                            continue
                        _, _, action, subtype, _ = headers[0]
                        attempt.counter = signatures[0]
                        parsed = lq.parse_test_trade_response(rows)
                        parsed.rejected = action in ("REJECTED", "EQUALIZE_FAILED") or subtype == "AI_REFUSE_DEAL"
                        report = narrate_test_trade(parsed, include_guidance=False, preview=attempt.preview)
                        if attempt.preview:
                            attempt.result = report
                            attempt.phase = "PREVIEW_EXIT_READY"
                        # INSPECT only evaluates terms, even for AI_ACCEPT_DEAL.
                        # Only the response to our later ACCEPTED can be final.
                        elif attempt.phase == "ACCEPT_WAIT":
                            if subtype not in ("AI_ACCEPT_DEAL", "HUMAN_ACCEPT_DEAL") or action != "ACCEPTED":
                                return self.trade_summary()
                            attempt.result = "ACCEPTED|Native response confirms the explicitly authorized trade.\n" + report
                            attempt.phase = "DIALOGUE"
                            return attempt.result + "\nInspect get_pending_diplomacy and use its current native choices; no response was chosen automatically."
                        elif action in ("REJECTED", "EQUALIZE_FAILED") or subtype == "AI_REFUSE_DEAL":
                            attempt.result = "EVALUATION_REJECTED|AI evaluation did not accept these terms; no trade concluded.\n" + report
                            attempt.phase = "COUNTER"
                            return attempt.result + "\nUse respond_to_trade(accept=False) to exit this evaluation; no final dialogue was inferred."
                        elif attempt.counter != attempt.original:
                            attempt.result = "COUNTER_OFFER|Fresh native counter differs from your proposal.\n" + report
                            attempt.phase = "COUNTER"
                            return attempt.result + "\nUse get_pending_trades, then respond_to_trade(accept=True/False) for an explicit decision."
                        else:
                            attempt.phase = "ACCEPT_READY"
                    if attempt.phase in ("ACCEPT_READY", "ACCEPT_ENTERED"):
                        if attempt.phase == "ACCEPT_READY":
                            armed = await _named(gs.conn, "DiplomacyActionView", native.build_trade_arm_action(self.nonce, attempt.token, "ACCEPTED"))
                            if armed != ["TRADE|ARMED"]:
                                return self.trade_summary()
                            attempt.phase = "ACCEPT_ENTERED"
                            rows = await _named(gs.conn, "DiplomacyDealView", native.build_trade_accept(self.observer_scope, attempt.target, attempt.sid, attempt.token, attempt.counter))
                            if any(row.startswith("TRADE_ACCEPT|NOT_SUBMITTED|") or row == "TRADE_DEAL|NOT_READY" for row in rows):
                                attempt.phase = "ACCEPT_NOT_SUBMITTED"
                        if attempt.phase != "ACCEPT_NOT_SUBMITTED":
                            attempt.phase = "ACCEPT_WAIT"  # Even a lost callback reply only observes its statement.
                            continue
                    if attempt.phase == "ACCEPT_NOT_SUBMITTED":
                        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_restore_evaluation(self.nonce, attempt.token))
                        if rows != ["TRADE|EVALUATION_RESTORED"]:
                            return self.trade_summary()
                        attempt.phase = "COUNTER"
                        return "ERR:NOT_SUBMITTED|Native acceptance guard changed; evaluation retained for an explicit decision or exit."
                    if attempt.phase in ("PREVIEW_EXIT_READY", "PREPARE_EXIT_READY"):
                        exit_phase = attempt.phase
                        if exit_phase == "PREPARE_EXIT_READY":
                            bound = await _named(gs.conn, "DiplomacyActionView", native.build_trade_session(self.nonce, attempt.token))
                            if bound != [f"TRADE_SESSION|BOUND|{attempt.sid}"]:
                                return self.trade_summary()
                        attempt.phase = "EXIT_ENTERED"
                        builder = native.build_trade_abort_prepare if exit_phase == "PREPARE_EXIT_READY" else native.build_trade_close_preview
                        rows = await _named(gs.conn, "DiplomacyDealView", builder(self.observer_scope, attempt.target, attempt.sid, attempt.token))
                        if "TRADE_DEAL|NOT_READY" in rows or "TRADE_EXIT|NOT_SUBMITTED" in rows:
                            attempt.phase = exit_phase
                            return self.trade_summary()
                    if attempt.phase in ("EXIT_ENTERED", "DIALOGUE", "COUNTER"):
                        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_closed(self.nonce, attempt.token))
                        if rows != ["TRADE_CLOSED|CONFIRMED"]:
                            if attempt.phase in ("DIALOGUE", "COUNTER"):
                                return (attempt.result or "") + "\n" + self.trade_summary()
                            await asyncio.sleep(TRADE_POLL_SECONDS)
                            continue
                        receipt = await _named(gs.conn, "DiplomacyDealView", native.build_trade_deal_receipt(self.observer_scope, attempt.token))
                        if "TRADE_VIEW|HIDDEN" not in receipt:
                            await asyncio.sleep(TRADE_POLL_SECONDS)
                            continue
                        attempt.phase = "CLEANUP"
                    if attempt.phase == "CLEANUP":
                        cleanup = await gs._diplomacy_cleanup.close(gs.conn)
                        if cleanup.status != "confirmed":
                            return (attempt.result or "") + "\n" + cleanup.message
                        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_finish(self.nonce, attempt.token))
                        if rows != ["TRADE_FINISH|CONFIRMED"]:
                            return self.trade_summary()
                        result = (attempt.result or "Native trade session closed.") + "\n" + cleanup.message
                        self.trade = None
                        return result
        except (OSError, TimeoutError, LuaError, ValueError, IndexError):
            pass
        return (attempt.result + "\n" if attempt.result else "") + self.trade_summary()

    async def explicit_cleanup(self, gs) -> str:
        if self.trade is None:
            return "No trade attempt pending."
        if self.trade.phase not in ("DIALOGUE", "COUNTER", "TERMS_ENTERED", "PREPARE_EXIT_READY", "ACCEPT_NOT_SUBMITTED", "EXIT_ENTERED", "CLEANUP"):
            return self.trade_summary()
        return await self.resume_trade(gs)

    async def respond_counter(self, gs, target: int, accept: bool) -> str | None:
        attempt = self.trade
        if attempt is None:
            return None
        if attempt.target != target:
            return self.trade_summary()
        if attempt.phase != "COUNTER":
            return await self.resume_trade(gs) if attempt.phase == "ACCEPT_NOT_SUBMITTED" else self.trade_summary()
        rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_response(self.nonce, attempt.token))
        signatures = [row.split("|", 1)[1] for row in rows if row.startswith("TRADE_COUNTER|")]
        if signatures != [attempt.counter]:
            return "ERR:NOT_SUBMITTED|Current counter changed or is unknown; no response submitted."
        if not accept:
            attempt.result = "DECLINED|Explicitly declined the current native counter."
        attempt.phase = "ACCEPT_READY" if accept else "PREVIEW_EXIT_READY"
        return await self.resume_trade(gs)

    async def outgoing_report(self, conn) -> str:
        attempt = self.trade
        if attempt is None or attempt.phase != "COUNTER" or self.scope_invalid:
            return ""
        rows = await _named(conn, "DiplomacyActionView", native.build_trade_response(self.nonce, attempt.token))
        signatures = [row.split("|", 1)[1] for row in rows if row.startswith("TRADE_COUNTER|")]
        return attempt.result if signatures == [attempt.counter] else ""



@dataclass
class TradeAttempt:
    token: str
    target: int
    preview: bool
    offer: list[dict]
    request: list[dict]
    phase: str = "OPEN_ENTERED"
    sid: int | None = None
    original: str | None = None
    counter: str | None = None
    result: str | None = None


TRADE_CONFIRM_SECONDS = 5.0
TRADE_POLL_SECONDS = 0.1


async def _named(conn, context: str, source: str) -> list[str]:
    return await conn.execute_in_named_state(context, source)
