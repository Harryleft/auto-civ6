# 工程验收索引

本目录保留项目能力的离线验证、覆盖口径和独立审查依据。单局实机的回合、决策、存档、耗时、恢复和终局记录，按对局及轮次保存在 [games/](../../games/README.md)。混合了代码验证与实机过程的原报告整体归档，提交号、测试结果及限制保持原记录，可从对应轮次的 `reports/` 查阅。

| 主题 | 记录 |
| --- | --- |
| 状态变更生命周期 | [全量覆盖与双轴审查](2026-10-19-mutation-lifecycle.md)、[使者试点](2026-10-19-envoy-lifecycle.md) |
| 状态归属与确认边界 | [架构深化](2026-10-17-architecture-deepening.md)、[回合世界身份](2026-10-18-end-turn-world-scope.md)、[来约零提交与失效锁存](2026-10-19-incoming-p2-boundaries.md) |
| 知识与决策输入 | [版本知识](2026-10-17-civ6-knowledge.md)、[主题扩展](2026-10-17-knowledge-expansion.md)、[知识 MCP](2026-10-18-knowledge-mcp.md)、[文明特性](2026-10-18-civilization-knowledge.md)、[获胜策略](2026-10-18-win-strategy.md) |
| 宗教防御 | [离线观测、清除异教与神学攻击](2026-10-20-religion-defense.md)、[原局检查点实机验收](../../games/2026-10-19-gold/runs/2026-10-19-first/religion-native-validation-2026-10-20/acceptance.md) |
| CI 测试替身 | [未决调度同步](2026-10-18-ci-pending-dispatch.md) |
| 测试效益与耗时 | [全量分类、慢测试及临时提速实验](2026-10-20-test-efficiency-audit.md) |

这里的通过记录只适用于对应受测版本；当前任务按[开发与验证](../development.md#离线验证)选择必要检查。
