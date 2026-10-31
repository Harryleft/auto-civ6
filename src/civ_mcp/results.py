"""Domain outcomes travel beside presentation; legacy text has one adapter.

ResultText preserves the in-process string interface during migration. The MCP
adapter carries its explicit outcome separately, so consumers never need to
recover a migrated owner's decision from its prose. It owns no pending state.
"""
from __future__ import annotations

from functools import wraps
import json
import re


class ResultText(str):
    def __new__(cls, message: str, *, kind: str, status: str, code: str | None = None,
                data: dict | None = None):
        value = super().__new__(cls, message)
        value.outcome = {"version": 1, "kind": kind, "status": status,
                         "code": code, "data": data or {}}
        return value

    def append(self, suffix: str) -> ResultText:
        return ResultText(str(self) + suffix, **{key: self.outcome[key]
                          for key in ("kind", "status", "code", "data")})


def query_result(payload: dict) -> ResultText:
    status = payload.get("status", "observed")
    # Outcome certainty and CLI error signalling are separate. Existing brief
    # reports can be incomplete without declaring a tool error.
    code = ("QUERY_UNCONFIRMED" if status == "unknown" else "TOOL_REPORTED_ERROR") if (
        status in {"unknown", "error"} and payload.get("error")) else None
    return ResultText(json.dumps(payload, ensure_ascii=False, allow_nan=False),
                      kind="query", status=status, code=code, data=payload)


def result_tool(mcp, **options):
    """Register the wire adapter while retaining direct-call compatibility."""
    def decorate(fn):
        @wraps(fn)
        async def wire(*args, **kwargs):
            result = await fn(*args, **kwargs)
            if not isinstance(result, ResultText):
                return result
            from mcp.types import CallToolResult, TextContent
            return CallToolResult(content=[TextContent(type="text", text=str(result))],
                                  structuredContent={"result": str(result), "outcome": result.outcome})
        mcp.tool(**options)(wire)
        return fn
    return decorate


def explicit_outcome(result: dict) -> dict | None:
    structured = result.get("structuredContent")
    value = structured.get("outcome") if isinstance(structured, dict) else None
    if (isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1
            and value.get("kind") in {"query", "action", "protocol"}
            and isinstance(value.get("status"), str) and isinstance(value.get("data"), dict)
            and "code" in value and (value["code"] is None or isinstance(value["code"], str))):
        return value
    return None


def _has_outcome(result: dict) -> bool:
    return isinstance(result.get("structuredContent"), dict) and "outcome" in result["structuredContent"]


def result_error(tool: str, result) -> dict | None:
    """One compatibility adapter for old MCP text and explicit domain outcomes."""
    payload = result.model_dump(mode="json")
    texts = [item.text for item in result.content if item.type == "text"]
    outcome = explicit_outcome(payload)
    if outcome is None and _has_outcome(payload):
        return {"code": "INVALID_DOMAIN_RESULT", "message": "Malformed domain outcome; result is unconfirmed",
                "hint": "Inspect the original response and pending; do not replay the request.",
                "details": {"tool": tool, "source": "domain_outcome"}}
    if outcome is not None:
        if not result.isError and outcome["code"] is None:
            return None
        code = outcome["code"] or "MCP_TOOL_ERROR"
        message = "\n".join(texts) or code
        source = "domain_outcome"
    else:
        legacy = next((text for text in texts if text.startswith(("Error:", "ERR:"))), None)
        structured = None
        for text in texts:
            try:
                value = json.loads(text)
                if isinstance(value, dict) and value.get("status") in {"error", "unknown"} and value.get("error"):
                    structured = value
                    break
            except ValueError:
                pass
        if not result.isError and legacy is None and structured is None:
            return None
        message = legacy or (str(structured["error"]) if structured else None) or "\n".join(texts) or "MCP reported a tool error without text"
        match = re.match(r"^(?:Error:\s*)?ERR:([A-Z][A-Z0-9_]*)\b", message)
        code = match[1] if match else "MCP_TOOL_ERROR" if result.isError else "TOOL_REPORTED_ERROR"
        if structured and structured.get("status") == "unknown":
            code = "QUERY_UNCONFIRMED" if tool.startswith(("get_", "search_")) else "OUTCOME_UNKNOWN"
        source = "protocol" if result.isError else "structured_json" if structured else "legacy_text"
    hint = f"Inspect the original error, tools {tool} and pending before correcting arguments; never blindly replay an unknown request."
    if code == "QUERY_UNCONFIRMED":
        hint = "Inspect identity, unknown_fields and error; unavailable data is not an empty list or zero."
    elif code == "NO_TILES":
        hint = "Use city production CITY_ID and current state to choose a different eligible city/district, then query city district CITY_ID DISTRICT_TYPE. No construction was submitted."
    elif code == "OUTCOME_UNKNOWN":
        hint = "Retain this session; use pending and read-only inspection, then confirm only through the original tool with its original arguments. Do not resubmit a new intent."
    return {"code": code, "message": message, "hint": hint,
            "details": {"tool": tool, "source": source}}


def observed_turn(result: dict) -> int | None:
    outcome = explicit_outcome(result)
    if outcome is not None:
        turn = outcome["data"].get("turn") if outcome["kind"] == "query" else None
        return turn if type(turn) is int and turn >= 0 and outcome["code"] is None else None
    if _has_outcome(result):
        return None
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    match = re.search(r"^Turn (\d+) \|", text, re.M)
    return int(match[1]) if match else None


def confirmed_advance(result: dict) -> tuple[int, int] | None:
    outcome = explicit_outcome(result)
    if outcome is not None:
        data = outcome["data"]
        before, after = data.get("from_turn"), data.get("to_turn")
        if (outcome["kind"] == "action" and outcome["status"] == "confirmed"
                and type(before) is int and type(after) is int and after > before):
            return before, after
        return None
    if _has_outcome(result):
        return None
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    match = re.search(r"^Turn (\d+) -> (\d+)(?:\n|$)", text)
    return (int(match[1]), int(match[2])) if match and int(match[2]) > int(match[1]) else None
