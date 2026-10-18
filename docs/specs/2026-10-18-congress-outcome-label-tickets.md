# 议会结果标签工作项

规格：[议会结果按原生所选标签展示](2026-10-18-congress-outcome-label.md)。ready-for-agent；沿用集成分支 `codex/cancel-unpathable-queue`。

| 项目 | 依赖 | 所有权与完成条件 | 状态 |
| --- | --- | --- | --- |
| R1 | 无 | 单一实施者修改议会原生查询、模型、展示与直接相关测试；红绿复现并修复 | ready |
| R2 | R1 | 独立 merger 合入并验证相关模块，保留精确版本及指纹 | blocked |
| R3 | R2 | Standards／Spec 双轴独立审查，实施者修复发现 | blocked |
| R4 | R3 | 父任务新 MCP 会话回读 T117 实际 B 结果，完成 T121 保存恢复及本地提交 | blocked |

父任务拥有规格、tracker 及验收文档；不修改其他工作项，不访问真实游戏，不 push。沿用已确认测试边界。
