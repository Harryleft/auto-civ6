# 世界身份绑定工作项

规格：[结束回合的世界身份绑定](2026-10-18-end-turn-world-scope.md)。标签 ready-for-agent；集成分支 `codex/end-turn-world-scope`，基点 `2f66909`。

| 工作项 | 依赖 | 所有权与完成条件 | 状态 |
| --- | --- | --- | --- |
| W1 身份绑定与回归 | 无 | 单一实施者负责 `end_turn.py`、必要原生 Lua 构造及 GameState 协作、直接相关测试；先红后绿，换世界保留门禁，同世界正常确认 | rework（初版已完成；补审查发现的异步取消失配锁存） |
| W2 独立合入与验证 | W1 | merger 独立合入并验证相关模块、compileall、diff 及源指纹 | 初版254项通过；待修复增量 |
| W3 双轴复查 | W2 | 两名独立 reviewer 对照规格及项目规范；问题由单一实施者修复 | in-progress（Standards初审通过，Spec发现P2取消窗口） |
| W4 验收、main 与保护核对 | W3 | 父任务记录离线结果、核对旧成果指纹、提交 main、清理实施 worktree，不 push | blocked |

沿用既有 GameState／FakeConn、Lua 5.1 与 MCP 边界，不实机、不全量。父任务负责规格、tracker、验收文档和必要指南更新，实施者不修改这些文档；不撤销其他人的改动。
