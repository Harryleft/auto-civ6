"""LangGraph 认知工作流：Jev 判断 + DeepSeek 决策 + Runtime 执行。

本包是 v7 方案唯一的认知主路径。它只通过 ``civ_mcp.runtime`` 的 Python 对象
读写游戏事实，不经过 MCP，也不依赖任何旧认知栈（belief / governance）。

正式路径不允许 ``observe → DeepSeek → execute``；Jev 必须经过。
"""

from __future__ import annotations

from civ_agent.config import AgentConfig, MissingCredentialError, load_config
from civ_agent.graph import GraphDeps, GraphError, GraphResources, build_graph
from civ_agent.mcp_client import (
    RuntimeClient,
    ToolCallError,
    ToolSpec,
    runtime_session,
    with_runtime,
)
from civ_agent.observation import Observation, OpponentState, build_observation
from civ_agent.rules import RuleHit, known_topics, search_rules
from civ_agent.state import (
    CandidateAction,
    ExecutionResult,
    ExecutionStatus,
    GraphState,
    MemoryHit,
    Seed,
    new_state,
)

__all__ = [
    "AgentConfig",
    "CandidateAction",
    "ExecutionResult",
    "ExecutionStatus",
    "GraphDeps",
    "GraphError",
    "GraphResources",
    "GraphState",
    "MemoryHit",
    "MissingCredentialError",
    "Observation",
    "OpponentState",
    "RuleHit",
    "RuntimeClient",
    "Seed",
    "ToolCallError",
    "ToolSpec",
    "build_graph",
    "build_observation",
    "known_topics",
    "load_config",
    "new_state",
    "runtime_session",
    "search_rules",
    "with_runtime",
]
