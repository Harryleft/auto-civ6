"""Owner outcomes survive presentation changes and the real MCP adapter."""
import asyncio
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from civ_mcp.results import ResultText, result_tool, result_error, confirmed_advance, observed_turn, query_result


def response(text, **outcome):
    result = ResultText(text, **outcome)
    return CallToolResult(content=[TextContent(type="text", text=str(result))],
                          structuredContent={"result": str(result), "outcome": result.outcome})


def test_domain_status_is_independent_of_prose_and_legacy_markers():
    result = response("显示文案已更换", kind="action", status="unknown", code="OUTCOME_UNKNOWN")
    assert result_error("unit_action", result)["code"] == "OUTCOME_UNKNOWN"
    confirmed = response("ERR: this is quoted evidence", kind="action", status="confirmed")
    assert result_error("unit_action", confirmed) is None
    assert result_error("unit_action", confirmed.model_copy(update={"isError": True}))["code"] == "MCP_TOOL_ERROR"


def test_explicit_timing_facts_override_text_and_never_promote_unknown():
    result = response("推进完成", kind="action", status="confirmed", data={"from_turn": 8, "to_turn": 9})
    assert confirmed_advance(result.model_dump(mode="json")) == (8, 9)
    unknown = response("Turn 8 -> 9\n", kind="action", status="unknown", code="OUTCOME_UNKNOWN")
    assert confirmed_advance(unknown.model_dump(mode="json")) is None
    overview = response("第九回合", kind="query", status="observed", data={"turn": 9})
    assert observed_turn(overview.model_dump(mode="json")) == 9


def test_malformed_outcome_cannot_fall_back_to_successful_prose():
    result = CallToolResult(content=[TextContent(type="text", text="Turn 8 -> 9\n")],
                           structuredContent={"outcome": {"version": 1, "kind": "action",
                               "status": "confirmed", "data": {"from_turn": 8, "to_turn": 9}}})
    assert result_error("end_turn", result)["code"] == "INVALID_DOMAIN_RESULT"
    assert confirmed_advance(result.model_dump(mode="json")) is None


def test_query_uncertainty_does_not_change_legacy_error_signalling():
    for payload in ({"status": "unknown", "report": "unknown facts"},
                    {"status": "unknown", "error": "native read failed"}):
        text = query_result(payload)
        result = CallToolResult(content=[TextContent(type="text", text=str(text))],
                               structuredContent={"result": str(text), "outcome": text.outcome})
        assert text.outcome["status"] == "unknown"
        assert bool(result_error("get_city_growth", result)) == ("error" in payload)


def test_action_log_keeps_owner_outcome_without_changing_original_text(tmp_path):
    from civ_mcp.logger import GameLogger
    path = tmp_path / 'actions.jsonl'
    text = ResultText('unchanged text', kind='action', status='confirmed')
    asyncio.run(GameLogger(path).log_tool_call('example', {}, text, 1))
    row = json.loads(path.read_text())
    assert row['result'] == 'unchanged text'
    assert row['outcome'] == text.outcome


def test_mcp_registration_preserves_text_direct_calls_schema_and_outcome():
    mcp = FastMCP("outcome-contract")

    @result_tool(mcp)
    async def example(number: int) -> str:
        return ResultText(f"原文本 {number}", kind="action", status="confirmed", data={"number": number})

    async def scenario():
        assert await example(3) == "原文本 3"
        tools = await mcp.list_tools()
        assert tools[0].inputSchema["required"] == ["number"]
        result = await mcp.call_tool("example", {"number": 3})
        assert result.content[0].text == "原文本 3"
        assert result.structuredContent["outcome"]["data"] == {"number": 3}
    asyncio.run(scenario())
