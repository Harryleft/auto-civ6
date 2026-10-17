# 本机版本世界知识工作项

规格：[本机世界知识](2026-10-17-civ6-knowledge.md)。本地 Markdown tracker；标签 ready-for-agent。基点 4c16f8f，沿用 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| K1 本机来源与版本 | 无 | 来源采集命令、快照、本机版本和保护清单；父任务 | done；29 表、7 段原生证据、4 项隔离 CLI 测试通过 |
| K2 规则构建与查询 | K1 契约 | 构建／查询命令、完整性检查、规则与清单、公开入口离线测试；独立实施 worktree | ready-for-agent |
| K3 四主题与渐进披露 | K1 契约 | 知识入口、四主题机制指南、后续优先级；独立实施 worktree | ready-for-agent |
| K4 集成与验收 | K1、K2、K3 | 协作入口、离线全量、复制后查询、双轴审查、修正与保护核对；父任务 | blocked |

K2 与 K3 依据同一来源契约并行，互不修改对方文件。每项只提交自身范围；合入前同步集成 tip。实施结束清理 worktree，不 push，不合并 main。
