# 攻击类别修复工作项

规格：[攻击类别与可用性](2026-10-18-terminal-attack-mode.md)。标签 ready-for-agent，集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| A1 类别与资格分离 | 无 | 单一实施任务：攻击与预览 Lua、真实 GameState／隔离 Lua 回归 | completed `341831c` / merge `f57ff4f` |
| A2 集成、恢复与续测 | A1 | 父任务：离线、独立审查、已确认检查点恢复、实机及终局续测 | completed：离线、双轴审查及原生第 244 回合终局验收通过 |

保留旧未知请求，不补造完成证据；本次恢复／重放单独计数。完成后清理实施 worktree，不 push。
