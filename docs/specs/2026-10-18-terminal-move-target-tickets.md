# 移动目标资格工作项

规格：[移动目标资格预检查](2026-10-18-terminal-move-target.md)。本地 Markdown tracker；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| M1 目标预检查与战争修饰 | 无 | 单一实施任务：移动 Lua、必要诊断及公开回归 | completed |
| M3 原生路径提交前保护 | M1 | 同一实施任务：路径和可见性检查及真实 Lua／GameState 回归 | ready-for-agent |
| M2 集成与实机续测 | M1、M3 | 父任务：全量离线、独立双轴审查、目标拒绝／合法移动与终局验证 | in-progress：M1 离线及审查通过，实机仍阻塞；按新证据补 M3 |

独立 worktree 实施；沿用既有测试边界。保留旧未知及全部存档，不用当前位置补造失败见证，不 push。
