# 世界身份绑定工作项

规格：[结束回合的世界身份绑定](2026-10-18-end-turn-world-scope.md)。标签 ready-for-agent；集成分支 `codex/end-turn-world-scope`，基点 `2f66909`。

| 工作项 | 依赖 | 所有权与完成条件 | 状态 |
| --- | --- | --- | --- |
| W1 身份绑定与回归 | 无 | 单一实施者负责 `end_turn.py`、必要原生 Lua 构造及 GameState 协作、直接相关测试；先红后绿，换世界保留门禁，同世界正常确认 | completed（含审查发现的取消窗口修复） |
| W2 独立合入与验证 | W1 | merger 独立合入并验证相关模块、compileall、diff 及源指纹 | completed（最终270项通过，7文件指纹不变） |
| W3 双轴复查 | W2 | 两名独立 reviewer 对照规格及项目规范；问题由单一实施者修复 | completed（初审P2已修复，两轴补审通过） |
| W4 验收、main 与保护核对 | W3 | 父任务记录离线结果、核对旧成果指纹、提交 main、清理实施 worktree，不 push | completed（验收随本次提交交付main，旧成果4649文件指纹一致） |

沿用既有 GameState／FakeConn、Lua 5.1 与 MCP 边界，不实机、不全量。父任务负责规格、tracker、验收文档和必要指南更新，实施者不修改这些文档；不撤销其他人的改动。

结果与限制见[离线验收](../acceptance/2026-10-18-end-turn-world-scope.md)。初版验证、审查发现和修复后证据分别保留。
