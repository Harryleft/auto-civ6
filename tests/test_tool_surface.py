"""The documented MCP tool surface must match the registry.

``AGENTS.md`` states the number of registered tools as prose ("``server/`` 包内
112 个工具"). A hand-maintained count drifts the moment a tool is added or
removed, and the operating instructions built on it quietly stop being true.
Pin the claim to the registry instead.

The pattern intentionally matches the *claim* (``server/`` … N 个工具) rather
than one exact sentence, so rewording the surrounding prose never silently
disarms this guard.
"""

from __future__ import annotations

import pathlib
import re

from civ_mcp.server import mcp

ROOT = pathlib.Path(__file__).resolve().parents[1]

_TOOL_COUNT_CLAIM = re.compile(r"server/\S*[^0-9\n]{0,20}?(\d+) 个工具")


def registered_tools() -> frozenset[str]:
    return frozenset(mcp._tool_manager._tools)


def test_agents_md_states_the_registered_tool_count():
    match = _TOOL_COUNT_CLAIM.search((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert match, (
        "AGENTS.md 不再声明工具数量；若措辞已改，请同步本测试的正则，"
        "否则该声称将失去守护"
    )
    assert int(match.group(1)) == len(registered_tools()), (
        f"AGENTS.md 声称 {match.group(1)} 个工具，实际注册 {len(registered_tools())} 个"
    )


def test_tool_names_are_unique_and_non_empty():
    names = list(mcp._tool_manager._tools)
    assert all(name.strip() for name in names)
    assert len(set(names)) == len(names)


def test_every_tool_is_reachable_through_the_server():
    """A registered tool must be exposed by FastMCP, not merely registered."""

    listed = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert listed == set(registered_tools())


def test_the_lean_surface_is_the_legacy_surface_minus_the_control_plane():
    """The lean profile narrows one documented axis and nothing else.

    ``AGENTS.md`` states the tool count for the default (legacy) profile. The
    lean profile is the same game surface with the belief/governance control
    plane withheld, so no game capability may disappear with it.
    """

    from civ_mcp.server.assembly import (
        LEAN_HIDDEN_CONTROL_TOOLS,
        PlayProfile,
        apply_play_profile,
    )

    legacy = set(registered_tools())
    change = apply_play_profile(PlayProfile.LEAN)
    try:
        lean = set(registered_tools())
    finally:
        change.restore()

    assert legacy - lean == set(LEAN_HIDDEN_CONTROL_TOOLS)
    assert lean < legacy, "精简模式不应新增任何工具"
    assert set(registered_tools()) == legacy
