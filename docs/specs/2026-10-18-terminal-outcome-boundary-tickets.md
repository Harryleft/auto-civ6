# 终局边界工作项

规格：[权威胜利类型与保存边界](2026-10-18-terminal-outcome-boundary.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| O1 权威终局事实与保存提交边界 | 无 | 单一实施任务：终局 Lua／解析、GameState 保存及必要原生保护、公开回归 | ready-for-agent |
| O2 集成与原生终局复验 | O1 | 父任务：全量离线、独立双轴审查、244 终局重放与存档保护核对 | ready-for-agent |

独立 worktree、tdd、独立 merger；不连接游戏，根任务独占实机。保留旧未知和全部存档，不 push。
