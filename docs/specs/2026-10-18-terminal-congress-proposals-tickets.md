# 议会提案工作项

规格：[显式提案投票](2026-10-18-terminal-congress-proposals.md)。标签 ready-for-agent；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| W1 完整提案查询与显式混合投票 | 无 | 单一实施任务：议会 Lua、模型／解析、叙述、工具契约与回归 | completed |
| W2 集成与实机续测 | W1 | 父任务：独立审查、第 167 回合复验及终局 | completed：离线、双轴审查及原生第 244 回合终局验收通过 |

沿用已确认测试边界；独立 worktree 实施，保留现场、所有存档及结果，合入后清理，不 push。

W1 已随 `ecfb0b7` 合入，中文及会后指引增量随 `909a867` 合入；离线、独立审查及第 167 回合实机结果见[验收](../acceptance/2026-10-18-terminal-congress-proposals.md)。全程已完成；见[终局验收](../acceptance/2026-10-18-turn-terminal.md)。
