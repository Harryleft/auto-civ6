# 工程验收索引

本目录保留项目能力的离线验证、覆盖口径和独立审查依据。单局实机的回合、决策、存档、耗时、恢复和终局记录，按对局及轮次保存在 [games/](../../games/README.md)。混合了代码验证与实机过程的原报告整体归档，提交号、测试结果及限制保持原记录，可从对应轮次的 `reports/` 查阅。

| 主题 | 记录 |
| --- | --- |
| CLI 唯一入口 | [取消直接注册、完整命名覆盖、开发边界及热座适配](2026-10-25-cli-only.md)、[JSON 明细](2026-10-25-cli-only.json) |
| 状态变更生命周期 | [全量覆盖与双轴审查](2026-10-19-mutation-lifecycle.md)、[使者试点](2026-10-19-envoy-lifecycle.md) |
| 状态归属与确认边界 | [架构深化](2026-10-17-architecture-deepening.md)、[回合世界身份](2026-10-18-end-turn-world-scope.md)、[来约零提交与失效锁存](2026-10-19-incoming-p2-boundaries.md) |
| 知识与决策输入 | [版本知识](2026-10-17-civ6-knowledge.md)、[主题扩展](2026-10-17-knowledge-expansion.md)、[知识 MCP](2026-10-18-knowledge-mcp.md)、[文明特性](2026-10-18-civilization-knowledge.md)、[获胜策略](2026-10-18-win-strategy.md) |
| 智能体知识利用 | [五个独立盲智能体：CLI 对照与仓库资料实验、访问证据及细节覆盖](2026-10-31-agent-knowledge-use/README.md)；离线小样本，不代表实战或总体性能 |
| CLI 短帮助与按需入口 | [字段与边界、原文迁移、五个新盲智能体复验](2026-10-31-cli-help-refinement/README.md)；统一资料输出下降34.01%，主评分不降，仍保留逐项遗漏与小样本限制 |
| 宗教防御 | [离线观测、清除异教与神学攻击](2026-10-20-religion-defense.md)、[原局检查点实机验收](../../games/2026-10-19-gold/runs/2026-10-19-first/religion-native-validation-2026-10-20/acceptance.md) |
| CI 测试替身 | [未决调度同步](2026-10-18-ci-pending-dispatch.md) |
| Windows 兼容 | [编码、路径、进程与分批离线验证](2026-10-20-windows-compatibility.md)、[神级净推进 10 回合](../../games/2026-10-19-gold/runs/2026-10-20-windows-compatibility-t001-t011/reports/windows-compatibility.md) |
| Windows 11 当前 CLI | [c63c814 修复复验：697 通过、0 失败、3 跳过，中文安装及 Ctrl+C 通过](2026-10-27-windows11-cli-fixes.md)、[修复摘要](2026-10-27-windows11-cli-fixes.json)、[19afac9 首轮失败对照](2026-10-27-windows11-cli.md) |
| Windows 稳定性续查 | [旧 MCP 超时与会话清理联动退出](2026-10-20-windows-stability-followup.md)、[第 11→21 回合与断开重连](../../games/2026-10-19-gold/runs/2026-10-20-windows-compatibility-t001-t011/reports/windows-stability-followup.md) |
| Windows 完整对局复核 | [问题、修复、测试及遗留项汇总](2026-10-21-windows-full-game-review.md)、[第 243 回合原生外交胜利](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/final.md) |
| 文化伟人创作 | [普通次数为零的修复、443 项回归与五件巨作实机验收](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/2026-10-21-great-work-creation.md) |
| 测试效益与耗时 | [全量分类、慢测试及临时提速实验](2026-10-20-test-efficiency-audit.md)、[替身修复与全量耗时对比](2026-10-20-test-efficiency-optimization.md) |
| 离线调试 CLI | [doctor、知识验证、领域测试与日志查询](2026-10-24-offline-cli.md) |
| CLI 文档与规则收尾 | [完整命令手册、CLI 默认入口及工作区清理预览](2026-10-24-cli-documentation-closeout.md) |
| 历史高频 CLI | [4,293 次调用统计、27 项新增快捷命令及实机检查](2026-10-24-cli-frequency.md) |
| CLI 领域化与自动实机验收 | [领域子命令、结构化快照与状态验收](2026-10-24-cli-domains.md) |
| 决策事实与效率 | [合并读取、重复文本压缩、485 项定向测试与四叶草 T1→T11 对照](../../games/2026-10-29-decision-efficiency/runs/20261029-2307/reports/decision-efficiency.md) |
| CLI 接口约定 | [组合、dry-run、分层帮助与错误修正](2026-10-24-cli-contract.md) |
| 前台调试会话 | [同一 MCP 进程、pending 查看、显式调用与退出](2026-10-24-foreground-debug.md) |

这里的通过记录只适用于对应受测版本；当前任务按[开发与验证](../development.md#离线验证)选择必要检查。
