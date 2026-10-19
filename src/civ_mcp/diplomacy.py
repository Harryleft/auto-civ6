"""Owned observations and one explicit native diplomacy response at a time."""
from __future__ import annotations

import asyncio
import hashlib
import re
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
        self.incoming = None
        self.response: tuple[str, int, int, int] | None = None

    @property
    def pending(self) -> bool:
        return self.response is not None or self.trade is not None or self.incoming is not None

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

    def _incoming_world_allowed(self, gs, rows: list[str]) -> bool:
        worlds = [row.split("|", 1)[1] for row in rows if row.startswith("INCOMING_WORLD|")]
        # Hand each witnessed world to the turn owner before another await.
        allowed = [gs.allows_incoming_trade_world(world) for world in worlds] if gs is not None else []

        def identity(scope):
            match = re.fullmatch(r"(-?\d+)\|(CIVILIZATION_[A-Z0-9_]+)\|(-?\d+)", scope or "")
            if match is None or not 0 <= int(match[1]) <= 63:
                return None
            return int(match[1]), match[2], int(match[3])

        expected = identity(self.incoming.scope if self.incoming is not None else self.observer_scope)
        observed = [identity(world) for world in worlds]
        # A complete foreign identity is permanent evidence against this attempt.
        # Missing/unknown identity only prevents this read from confirming it.
        if self.incoming is not None and expected is not None and any(world is not None and world != expected for world in observed):
            self.scope_invalid = True
        return (not self.scope_invalid and expected is not None and observed == [expected]
                and all(allowed))

    async def incoming_quote(self, conn, sid: int, target: int, *, gs=None, final: bool = False) -> dict | None:
        if self.scope_invalid or (self.incoming is None and not await self.ensure_observer(conn)):
            return None
        try:
            rows = await _named(conn, "DiplomacyDealView", native.build_incoming_quote(self.observer_scope, target, sid, final=final))
            if not self._incoming_world_allowed(gs, rows):
                return None
            data = {}
            for name in ("SCOPE", "SESSION", "PHASE", "ORIGINAL", "CURRENT"):
                values = [row.split("|", 1)[1] for row in rows if row.startswith("INCOMING_" + name + "|")]
                if len(values) != 1:
                    return None
                data[name.lower()] = values[0]
            if data["scope"] != self.observer_scope or data["session"] != f"{sid}|{target}":
                return None
            if final:
                kinds = [row.split("|", 1)[1] for row in rows if row.startswith("INCOMING_DEMAND|")]
                if kinds not in (["true"], ["false"]):
                    return None
                data["demand"] = kinds == ["true"]
            data["fingerprint"] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
            return data
        except (OSError, TimeoutError, LuaError):
            return None

    async def describe_incoming(self, conn, context) -> None:
        if context.status != "INCOMING" or len(context.sessions) != 1:
            return
        session = context.sessions[0]
        quote = await self.incoming_quote(conn, session.session_id, session.other_player_id)
        if quote is None:
            context.negotiation_summary = "Negotiation UNKNOWN; no current acceptance or counteroffer is authorized."
            return
        context.negotiation_fingerprint = quote["fingerprint"]
        context.negotiation_phase = quote["phase"]
        context.negotiation_summary = (
            f"Negotiation {quote['phase']} — session {session.session_id}, player {session.other_player_id}\n"
            f"fingerprint={quote['fingerprint']}\n"
            f"incoming_terms={quote['original']}\ncurrent_terms={quote['current']}\n"
            "Raw term fields: owner,type,value,subtype,amount,duration. Edited/evaluated terms are not a concluded deal. "
            "Use negotiate_incoming_trade for explicit no-commit evaluation; final acceptance requires native ACCEPTABLE terms."
        )

    async def negotiate_incoming(self, gs, target: int, fingerprint: str, edits: dict, mode: str) -> str:
        if self.scope_invalid:
            return self.trade_summary()
        if self.incoming is not None:
            if target != self.incoming.target:
                return self.trade_summary()
            return await self.confirm_incoming(gs)
        if self.trade is not None or self.response is not None or gs.save_pending or gs.replacement_pending or gs.pending_action_summary or gs._diplomacy_cleanup.pending:
            return "ERR:NOT_SUBMITTED|Another action, save, replacement or diplomacy attempt is pending."
        if mode not in ("inspect", "equalize") or not isinstance(fingerprint, str) or not fingerprint:
            return "ERR:NOT_SUBMITTED|A current fingerprint and inspect/equalize mode are required."
        if any(type(value) is not int or value < 1 or value > 2147483647 for value in edits.values()):
            return "ERR:NOT_SUBMITTED|Gold amounts must be positive integers."
        context = await gs.get_pending_deals()
        if context.status != "INCOMING" or len(context.sessions) != 1 or context.sessions[0].other_player_id != target:
            return "ERR:NOT_SUBMITTED|No verified current incoming negotiation for this target."
        sid = context.sessions[0].session_id
        quote = await self.incoming_quote(gs.conn, sid, target)
        if quote is None or quote["fingerprint"] != fingerprint:
            return "ERR:NOT_SUBMITTED|Quote changed or is unknown; read current incoming terms again."
        if not gs.allows_incoming_trade_world(quote["scope"]):
            return "ERR:NOT_SUBMITTED|Pending end-turn world is changed or unbound; no incoming evaluation submitted."
        attempt = IncomingAttempt(uuid.uuid4().hex, target, sid, quote["scope"], mode.upper())
        self.incoming = attempt
        try:
            armed = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_arm(self.nonce, attempt.token, target, sid, attempt.action))
            if not self._incoming_world_allowed(gs, armed):
                return self.trade_summary()
            arm_status = [row for row in armed if row.startswith("INCOMING|")]
            if arm_status == ["INCOMING|NOT_ARMED"]:
                self.incoming = None
                return "ERR:NOT_SUBMITTED|Native session could not be armed."
            if arm_status != ["INCOMING|ARMED"]:
                return self.trade_summary()
            if not gs.allows_incoming_trade_world(attempt.scope):
                return self.trade_summary()
            rows = await _named(gs.conn, "DiplomacyDealView", native.build_incoming_send(attempt.scope, target, sid, attempt.token, quote["original"], quote["current"], quote["phase"], edits, attempt.action))
            if not self._incoming_world_allowed(gs, rows):
                return self.trade_summary()
            if any(row.startswith("INCOMING|NOT_SUBMITTED|") for row in rows) or "INCOMING|NOT_READY" in rows:
                finished = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_finish(self.nonce, attempt.token, zero=True))
                if not self._incoming_world_allowed(gs, finished):
                    return self.trade_summary()
                if "INCOMING_FINISH|CONFIRMED" in finished:
                    self.incoming = None
                    return "ERR:NOT_SUBMITTED|Native guard rejected the unchanged quote/amount; no evaluation sent. " + " ".join(rows)
        except (OSError, TimeoutError, LuaError):
            return self.trade_summary()
        return await self.confirm_incoming(gs)

    async def confirm_incoming(self, gs) -> str:
        attempt = self.incoming
        if attempt is None or self.scope_invalid or not gs.allows_incoming_trade_world(attempt.scope):
            return self.trade_summary()
        if attempt.action in ("ACCEPTED", "REJECTED"):
            return await self.confirm_incoming_final(gs)
        try:
            async with asyncio.timeout(TRADE_CONFIRM_SECONDS):
                while True:
                    receipt = await _named(gs.conn, "DiplomacyDealView", native.build_incoming_receipt(attempt.scope, attempt.target, attempt.sid, attempt.token))
                    if not self._incoming_world_allowed(gs, receipt):
                        return self.trade_summary()
                    if "INCOMING_RECEIPT|ZERO" in receipt:
                        # The DealView retained proof that editing was rolled
                        # back before SendWorkingDeal, even if its reply was lost.
                        finished = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_finish(self.nonce, attempt.token, zero=True))
                        if not self._incoming_world_allowed(gs, finished) or "INCOMING_FINISH|CONFIRMED" not in finished:
                            return self.trade_summary()
                        self.incoming = None
                        return "ERR:NOT_SUBMITTED|Native edit was rejected and the original draft restored; no evaluation sent."
                    proposals = [row.split("|", 1)[1] for row in receipt if row.startswith("INCOMING_PROPOSAL|")]
                    if "INCOMING_RECEIPT|SENT" not in receipt or len(proposals) != 1:
                        return self.trade_summary()
                    rows = await _named(gs.conn, "DiplomacyActionView", native.build_trade_response(self.nonce, attempt.token, incoming=True))
                    if not self._incoming_world_allowed(gs, rows):
                        return self.trade_summary()
                    headers = [row.split("|") for row in rows if row.startswith("TRADE_RESPONSE|KNOWN|")]
                    signatures = [row.split("|", 1)[1] for row in rows if row.startswith("TRADE_COUNTER|")]
                    if len(headers) != 1 or len(signatures) != 1:
                        await asyncio.sleep(TRADE_POLL_SECONDS)
                        continue
                    action, subtype = headers[0][2:4]
                    quote = await self.incoming_quote(gs.conn, attempt.sid, attempt.target, gs=gs)
                    if (quote is None or not gs.allows_incoming_trade_world(quote["scope"])
                            or quote["original"] != signatures[0] or quote["current"] != signatures[0]):
                        return self.trade_summary()
                    if action in ("REJECTED", "EQUALIZE_FAILED") or subtype == "AI_REFUSE_DEAL":
                        outcome = "REJECTED"
                    elif signatures[0] != proposals[0] and action in ("ACCEPTED", "PROPOSED", "ADJUSTED") and quote["phase"] == "ACCEPTABLE":
                        outcome = "COUNTER_OFFER"
                    elif (action in ("ACCEPTED", "PROPOSED", "ADJUSTED") and quote["phase"] == "ACCEPTABLE"
                          and (subtype == "AI_ACCEPT_DEAL" or attempt.action == "EQUALIZE")):
                        # Official MakeDeal_ApplyStatement updates an evaluation
                        # from DealAction even with subtype NONE. Equalize may
                        # return the exact proposed terms, so no changed counter
                        # or conversational AI_ACCEPT_DEAL subtype is required.
                        outcome = "WILLING"
                    else:
                        return self.trade_summary()
                    finished = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_finish(self.nonce, attempt.token))
                    if not self._incoming_world_allowed(gs, finished) or "INCOMING_FINISH|CONFIRMED" not in finished:
                        return self.trade_summary()
                    self.incoming = None
                    parsed = lq.parse_test_trade_response(rows)
                    parsed.rejected = outcome == "REJECTED"
                    if outcome == "WILLING":
                        # Equal native fingerprints certify these are our exact
                        # evaluated terms, rather than a distinct fair counter.
                        parsed.proposed = list(parsed.counter)
                    report = narrate_test_trade(parsed, include_guidance=False, preview=True)
                    return f"{outcome}|Native {attempt.action} evaluation; deal not concluded.\n" + report + f"\nfingerprint={quote['fingerprint']}\nUse a fresh query for another explicit offer or final response."
        except (OSError, TimeoutError, LuaError, ValueError, IndexError):
            return self.trade_summary()

    async def respond(self, conn, sid: int, target: int, response: str) -> str:
        if self.incoming is not None:
            return self.trade_summary()
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
        if self.incoming is not None:
            tool = "respond_to_deal" if self.incoming.action in ("ACCEPTED", "REJECTED") else "negotiate_incoming_trade"
            return f"ERR:OUTCOME_UNKNOWN|Incoming {self.incoming.action} with player {self.incoming.target} is pending; repeat {tool} only confirms, never resends."
        if self.trade is not None:
            return (f"ERR:OUTCOME_UNKNOWN|Trade attempt {self.trade.phase} with player {self.trade.target} is pending. "
                    "Repeat the original trade tool only to confirm; no RequestSession, proposal or response will be resent.")
        return "ERR:OUTCOME_UNKNOWN|Native diplomacy response is pending; repeat response only to confirm."

    async def diagnostics(self, conn) -> str:
        """Read the existing observer only; never install, advance, or retire it."""
        attempt = self.incoming or self.trade
        if attempt is None:
            return ""
        if self.scope_invalid or self.observer_scope is None:
            return "diplomacy_observation: world=UNKNOWN reason=SCOPE_UNCONFIRMED"
        try:
            rows = await conn.execute_in_named_state("DiplomacyActionView", native.build_trade_diagnostics(self.nonce, self.observer_scope, attempt.token, attempt.sid))
            facts = [row.split("|", 1)[1] for row in rows if row.startswith("DIPLO_DIAGNOSTIC|")]
            if facts:
                if self.incoming is not None:
                    try:
                        receipt = await _named(conn, "DiplomacyDealView", native.build_incoming_receipt(
                            attempt.scope, attempt.target, attempt.sid, attempt.token))
                        facts.append("incoming_evaluation_receipt: diagnostic_only=true " + " ".join(receipt))
                    except (OSError, TimeoutError, LuaError):
                        facts.append("incoming_evaluation_receipt: UNKNOWN reason=READ_FAILED diagnostic_only=true")
                return "\n".join(facts)
        except (OSError, TimeoutError, LuaError):
            return "diplomacy_observation: UNKNOWN reason=READ_FAILED"
        return "diplomacy_observation: UNKNOWN reason=NO_DIAGNOSTIC_RECEIPT"

    async def run_trade(self, gs, target: int, offer: list[dict], request: list[dict], *, preview: bool) -> str:
        # A retained receipt belongs only to the prepared request. Check before
        # any confirmation/cleanup can turn its result into a new call's reply.
        if self.trade is not None and (
            target != self.trade.target or preview != self.trade.preview
            or offer != self.trade.offer or request != self.trade.request
        ):
            mode = "test_trade" if self.trade.preview else "propose_trade"
            return (f"ERR:PENDING_TRADE_MISMATCH|Existing trade with player {self.trade.target}, mode={mode}, "
                    f"phase={self.trade.phase}; new request not submitted. "
                    "Repeat the original target, mode, offer and request to confirm.")
        if self.incoming is not None:
            return self.trade_summary()
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
                        failures = [row.split("|", 2)[2] for row in rows if row.startswith("TRADE_PREPARE|NOT_SUBMITTED|")]
                        if failures:
                            attempt.result = "ERR:NOT_SUBMITTED|" + failures[0] + "; no proposal or equalize was sent."
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

    async def respond_incoming(self, gs, target: int, accept: bool) -> str:
        action = "ACCEPTED" if accept else "REJECTED"
        if self.incoming is not None:
            if self.incoming.target == target and self.incoming.action == action:
                return await self.confirm_incoming_final(gs)
            return self.trade_summary()
        if self.scope_invalid or self.trade is not None or self.response is not None or gs.save_pending or gs.replacement_pending or gs.pending_action_summary or gs._diplomacy_cleanup.pending:
            return "ERR:NOT_SUBMITTED|Another action or diplomacy attempt is pending."
        context = await gs.get_pending_deals()
        if context.status != "INCOMING" or len(context.sessions) != 1 or context.sessions[0].other_player_id != target:
            return f"ERR:NOT_SUBMITTED|{context.status}: {context.reason}; no verified incoming negotiation for this target."
        sid = context.sessions[0].session_id
        quote = await self.incoming_quote(gs.conn, sid, target, final=True)
        if quote is None or not gs.allows_incoming_trade_world(quote["scope"]):
            return "ERR:NOT_SUBMITTED|Incoming quote or original world is unknown."
        if accept and quote["phase"] != "ACCEPTABLE":
            return "ERR:NOT_ACCEPTABLE|Current native incoming terms cannot be accepted."
        attempt = IncomingAttempt(uuid.uuid4().hex, target, sid, quote["scope"], action, demand=quote["demand"], original=quote["original"], current=quote["current"])
        self.incoming = attempt
        try:
            rows = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_arm(self.nonce, attempt.token, target, sid, action))
            if not self._incoming_world_allowed(gs, rows):
                return self.trade_summary()
            statuses = [row for row in rows if row.startswith("INCOMING|")]
            if statuses == ["INCOMING|NOT_ARMED"]:
                self.incoming = None
                return "ERR:NOT_SUBMITTED|Original incoming session could not be armed."
            if statuses != ["INCOMING|ARMED"]:
                return self.trade_summary()
            rows = await _named(gs.conn, "DiplomacyDealView", native.build_incoming_final_send(attempt.scope, target, sid, attempt.token, quote["original"], quote["current"], accept, demand=attempt.demand))
            if not self._incoming_world_allowed(gs, rows):
                return self.trade_summary()
            if any(row.startswith("INCOMING|NOT_SUBMITTED|") for row in rows) or "INCOMING|NOT_READY" in rows:
                finished = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_finish(self.nonce, attempt.token, zero=True))
                if self._incoming_world_allowed(gs, finished) and "INCOMING_FINISH|CONFIRMED" in finished:
                    self.incoming = None
                    return "ERR:NOT_SUBMITTED|Incoming quote changed before response; no response sent."
        except (OSError, TimeoutError, LuaError):
            return self.trade_summary()
        return await self.confirm_incoming_final(gs)

    async def confirm_incoming_final(self, gs) -> str:
        attempt = self.incoming
        if attempt is None or self.scope_invalid or not gs.allows_incoming_trade_world(attempt.scope):
            return self.trade_summary()
        try:
            async with asyncio.timeout(TRADE_CONFIRM_SECONDS):
                while True:
                    receipt = await _named(gs.conn, "DiplomacyDealView", native.build_incoming_final_receipt(attempt.scope, attempt.target, attempt.sid, attempt.token, attempt.action, original=attempt.original, current=attempt.current, demand=attempt.demand))
                    if not self._incoming_world_allowed(gs, receipt) or "INCOMING_FINAL_RECEIPT|ENTERED" not in receipt:
                        return self.trade_summary()
                    rows = await _named(gs.conn, "DiplomacyActionView", native.build_incoming_final_confirmation(self.nonce, attempt.token, attempt.action, demand=attempt.demand))
                    if not self._incoming_world_allowed(gs, rows):
                        return self.trade_summary()
                    if "INCOMING_FINAL|CONFIRMED" in rows:
                        self.incoming = None
                        return f"DEAL_{attempt.action}|Native final response confirmed for player {attempt.target}."
                    await asyncio.sleep(TRADE_POLL_SECONDS)
        except (OSError, TimeoutError, LuaError):
            return self.trade_summary()

    async def respond_counter(self, gs, target: int, accept: bool) -> str | None:
        if self.incoming is not None:
            return await self.respond_incoming(gs, target, accept)
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
class IncomingAttempt:
    token: str
    target: int
    sid: int
    scope: str
    action: str
    demand: bool = False
    original: str | None = None
    current: str | None = None


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
