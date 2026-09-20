"""离线测试用的假 Runtime server 与事实夹具。

用真实的 ``FastMCP`` + 内存传输，因此测的是真正的 MCP 编解码与工具发现，
不需要子进程、FireTuner 或正在运行的游戏。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP


def fake_runtime_context() -> dict[str, Any]:
    """按 ``get_runtime_context`` 的真实 JSON 形状构造事实。

    形状依据 ``civ_mcp.runtime.server._json_value``：``facts`` 的每一项是
    ``{"value": ..., "source": ..., "observed_turn": ..., "coverage": ...}``。
    """

    return {
        "facts": {
            "overview": {
                "value": {
                    "turn": 7,
                    "player_id": 0,
                    "civ_name": "巴比伦",
                    "leader_name": "汉谟拉比",
                    "difficulty": "Emperor",
                    "num_cities": 1,
                    "total_population": 2,
                    "gold": 20.0,
                    "gold_per_turn": 3.0,
                    "science_yield": 4.5,
                    "culture_yield": 2.0,
                    "faith": 0.0,
                    "score": 15,
                },
                "source": "civ6:FireTuner",
                "observed_turn": 7,
                "coverage": "CURRENT_GAME:COMPLETE",
            },
            "cities": {
                "value": [{"city_id": 1, "name": "巴比伦", "population": 2}],
                "source": "civ6:FireTuner",
                "observed_turn": 7,
                "coverage": "CURRENT_GAME:COMPLETE",
            },
            "diplomacy": {
                "value": [
                    {
                        "player_id": 1,
                        "civ_name": "罗马",
                        "leader_name": "图拉真",
                        "has_met": False,
                        "is_at_war": False,
                    },
                    {
                        "player_id": 2,
                        "civ_name": "埃及",
                        "leader_name": "克利奥帕特拉",
                        "has_met": True,
                        "is_at_war": False,
                        "diplomatic_state": "FRIENDLY",
                        "num_cities": 2,
                        "military_strength": 90,
                    },
                ],
                "source": "civ6:FireTuner",
                "observed_turn": 7,
                "coverage": "MET_CIVILIZATIONS:COMPLETE;UNMET_CIVILIZATIONS:UNOBSERVED",
            },
            "tech_civics": {
                "value": {
                    "current_research": "Pottery",
                    "current_research_type": "TECH_POTTERY",
                    "current_civic": "Code of Laws",
                    "current_civic_type": "CIVIC_CODE_OF_LAWS",
                },
                "source": "civ6:FireTuner",
                "observed_turn": 7,
                "coverage": "RESEARCH_AND_CIVICS:COMPLETE",
            },
            "victory_progress": {
                "value": [
                    {
                        "player_id": 0,
                        "name": "巴比伦",
                        "score": 15,
                        "military_strength": 210,
                    }
                ],
                "source": "civ6:FireTuner",
                "observed_turn": 7,
                "coverage": "MET_CIVILIZATIONS:CURRENTLY_VISIBLE",
            },
        },
        "unknown": ["units: TimeoutError"],
        "pending_decisions": [],
        "unfinished_intents": [],
        "handoff": None,
        "further_queries": ["get_policies"],
    }


def build_fake_server() -> FastMCP:
    """一个最小 Runtime server：只读上下文、只读政策、一个 mutation、一个失败工具。"""

    server = FastMCP("Fake Runtime Core")

    @server.tool(annotations={"readOnlyHint": True})
    async def get_runtime_context() -> dict[str, Any]:
        """Return fresh facts, unknowns, unfinished operations, and handoff context."""
        return fake_runtime_context()

    @server.tool(annotations={"readOnlyHint": True})
    async def get_policies() -> dict[str, Any]:
        """Return policy facts."""
        return {
            "value": [{"policy_type": "POLICY_DISCIPLINE", "name": "纪律"}],
            "coverage": "OK",
        }

    @server.tool()
    async def move_unit(unit_index: int, target_x: int, target_y: int) -> dict[str, Any]:
        """Submit exactly one unit move."""
        return {
            "outcome": "OBSERVING",
            "unit_index": unit_index,
            "x": target_x,
            "y": target_y,
        }

    @server.tool(annotations={"readOnlyHint": True})
    async def explode() -> dict[str, Any]:
        """Always fail, to prove errors are not silently treated as data."""
        raise RuntimeError("FireTuner 不可用")

    return server
