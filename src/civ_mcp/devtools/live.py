"""Explicit, bounded live read-only verification through the foreground client."""

from __future__ import annotations

from civ_mcp.devtools.evidence import CommandError, Evidence
from civ_mcp.devtools.knowledge import check_interpreter


SNAPSHOT_CHECKS = {
    "identity_known", "world_stable", "player_turn_stable", "city_count", "unit_count_range",
    "unit_ids_unique", "city_ids_unique", "city_scope", "identity_matches_overview",
}


async def verify_session(debug, *, expected_turn=None, expected_civilization=None) -> dict:
    from civ_mcp.devtools.debug import strict_json
    result = {"feature_ids": ["state.snapshot", "state.identity", "session.pending"],
              "status": "BLOCKED", "scope": "live_readonly", "errors": []}
    try:
        runtime = await debug.runtime()
        if runtime["has_pending"] or runtime.get("shared_pending") or debug.transport_unknown:
            raise CommandError("VERIFY_PENDING", "State verification requires no unresolved requests in this MCP session",
                hint="Use pending and the original confirmation tool; verification will not confirm or clear the request.")
        # The existing overview endpoint encodes caught exceptions as "Error:"
        # text even when MCP isError is false. Recognize that failure boundary;
        # success still requires the structured native snapshot below.
        overview = await debug.call("get_game_overview", {})
        legacy_error = overview is not None and any(
            item.type == "text" and item.text.startswith("Error:") for item in overview.content)
        if overview is None or overview.isError or legacy_error:
            raise CommandError("LIVE_QUERY_FAILED", "Initial overview RPC failed", "FAIL",
                hint="Inspect the retained tool response and connection; no mutation has been sent.")
        snapshot = await debug.call("get_game_snapshot", {})
        if snapshot is None or snapshot.isError:
            raise CommandError("LIVE_QUERY_FAILED", "Structured snapshot RPC failed", "FAIL",
                hint="Inspect the retained tool response and connection; no mutation has been sent.")
        payload = strict_json("\n".join(item.text for item in snapshot.content if item.type == "text"))
        debug.evidence.json("verification-snapshot", payload)
        checks = payload.get("checks", {})
        if (payload.get("schema_version") != 1 or payload.get("scope") != "current_player"
                or payload.get("status") != "ready" or set(checks) != SNAPSHOT_CHECKS
                or any(value is not True for value in checks.values())):
            raise CommandError("SNAPSHOT_UNCONFIRMED", "Snapshot identity, count or scope checks were not all confirmed",
                hint="Inspect verification-snapshot.json for failed/unknown checks. Keep the current game; do not reload or replay actions automatically.",
                details={"status": payload.get("status"), "checks": checks, "error": payload.get("error")})
        data = payload["data"]
        identity = data["identity"]
        result.update(identity=identity, city_count=len(data["cities"]), listed_unit_count=len(data["units"]), checks=checks)
        if ((expected_turn is not None and identity["turn"] != expected_turn)
                or (expected_civilization is not None and data["overview"]["civilization_type"] != expected_civilization)):
            raise CommandError("UNEXPECTED_WORLD", "Observed game does not match the requested turn/civilization",
                hint="Check the current game and expected values. Load only the task's verified checkpoint through an explicitly authorized lifecycle command.",
                details={"observed": identity, "expected_turn": expected_turn, "expected_civilization": expected_civilization})
        after = await debug.runtime()
        if after["has_pending"] or after.get("shared_pending") or debug.transport_unknown:
            raise CommandError("VERIFY_PENDING", "Unresolved context exists after read-only verification",
                hint="Retain the session and inspect pending; read-only verification cannot release it.")
        result["status"] = "PASS"
    except CommandError as error:
        result.update(status=error.status, errors=[error.as_dict()])
    debug.state["verification"] = result
    debug.evidence.json("verification", result)
    debug.event("verification", **result)
    return result


def verify_live(evidence: Evidence, state: dict, *, timeout: float, expected_turn=None, expected_civilization=None) -> str:
    from civ_mcp.devtools.debug import keep_session_on_interrupt, run_session, serve
    check_interpreter(evidence.project)
    evidence.summary.update(mode="live", game_verification="unconfirmed_readonly")
    evidence.save()

    async def drive(debug):
        result = await verify_session(debug, expected_turn=expected_turn, expected_civilization=expected_civilization)
        return result["status"]

    with keep_session_on_interrupt():
        status = run_session(serve(evidence, state, offline=False, timeout=timeout,
                                  source=None, emit=lambda event: None, driver=drive))
    evidence.summary["game_verification"] = "passed_readonly" if status == "PASS" else "unconfirmed_readonly"
    if status != "PASS":
        errors = state.get("verification", {}).get("errors", [])
        if errors:
            error = errors[0]
            raise CommandError(error["code"], error["message"], status, hint=error["hint"], details=error["details"])
    return status
