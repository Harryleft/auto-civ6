# 政策跨槽修复工作项

规格：[政策跨槽调整](2026-10-18-terminal-policy-relocation.md)。标签 ready-for-agent；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| P1 完整政策计划 | 无 | 单一实施任务：政策 Lua 构建及公开 GameState／Lua 执行回归 | ready-for-agent |
| P2 集成与实机复验 | P1 | 父任务：全量离线、独立双轴审查、第 106 回合菜单恢复验证与终局续测 | blocked |

单一实施 worktree；完成后同步集成 tip，合入后清理工作树。不改知识目录、原始基准或既有验收。
