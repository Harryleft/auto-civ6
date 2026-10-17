# 五主题扩展工作项

规格：[五主题世界知识](2026-10-17-knowledge-expansion.md)。本地 Markdown tracker；状态 implementation-complete / review-fix；基点 71c4f64；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| E0 来源与共享查询 | 无 | 父任务：采集、构建、查询、既有测试适配、入口与正式生成资料 | completed `5c1b9ba` / `2f31c9f` / `aeb3d8f` / `d82043f`；审查修正另见 E6 |
| E1 经济与成长 | E0 来源契约 | 独立主题指南、机制定义、主题 CLI 回归测试 | completed `fefa64b` |
| E2 资源电力气候 | E0 来源契约 | 独立主题指南、机制定义、主题 CLI 回归测试 | completed `2200c79` |
| E3 战斗与移动 | E0 来源契约 | 独立主题指南、机制定义、主题 CLI 回归测试 | completed `0d63b63` |
| E4 外交与贸易 | E0 来源契约 | 独立主题指南、机制定义、主题 CLI 回归测试 | completed `89ef456` |
| E5 胜利与宗教 | E0 来源契约 | 独立主题指南、机制定义、主题 CLI 回归测试 | completed `d4eb869` |
| E6 集成验收 | E0～E5 | 全量离线、独立双轴审查、修正、保护校验与关闭记录 | in-progress：988 项离线通过；双轴审查发现来源关联与语言缺口，由单一实施任务修正 |

优先级 E1→E2→E3→E4→E5。最多三个实施子任务并发，完成即接续后项；共享模块由父任务维护。合入前同步集成 tip；完成后清理实施 worktree，保留分支，不合入 main、不 push。
