"""LangGraph 构建入口（M10 实现）。

节点与回边按 v7 §12 固定为：

``observe → retrieve_memory → jev_assess → deepseek_decide``，其中
``deepseek_decide`` 可回边到 ``search_rules`` / ``read_game_info``（后者回到
``observe`` 重新 Assess），随后 ``jev_review → deepseek_finalize → execute →
verify → append_game_memory → observe``。

正式路径不允许 ``observe → DeepSeek → execute``；Jev 必须经过。M10 之前这里
不返回任何图，避免调用方把未接线的图当成可用主路径。
"""

from __future__ import annotations

from typing import Any

_NOT_WIRED = (
    "LangGraph 尚未接线（M10）。方案要求 Jev 必须在正式路径中经过两次，"
    "在节点实现完成前不提供可运行的图。"
)


def build_graph(**kwargs: Any) -> Any:
    """构建认知工作流；节点接线完成前显式失败。"""

    raise NotImplementedError(_NOT_WIRED)
