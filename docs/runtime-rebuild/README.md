# Runtime Core Replacement

## 冻结基线

新 Runtime 的冻结基线是 Git 提交
`1d92a723b18609d734b6aacdda26ce815f390a21`，本地标记为
`runtime-core-pre-rebuild`。旧核心仍可通过该提交独立检出和复现。

这项重构不会在旧的 Belief / Governance / 多层 pipeline 运行链上继续增加
功能。旧链在新 Runtime 完成切换前仅接收阻止数据损坏、安全问题或无法启动的
紧急修复；新能力必须落在 `civ_mcp.runtime` 和 `civ_mcp.civ` 的目标依赖方向上。

## 重构范围与运行边界

目标是建立一条单一、可验证的执行路径：Civ6 保存世界事实，Runtime 保存操作
事实和连续性，模型负责游戏判断。切换前旧链保持只读的历史参考价值，不能成为
新 Runtime 的依赖或 fallback。

每张任务卡独立提交、独立验证。当前已完成 A0（冻结）；后续按
`A1 → B1 → B2 → C1 → C2` 建立 Transport 和 Operation 语义，再接入
`CivAdapter`、`SessionKernel` 和新的 MCP Surface。完整计划见项目外的
`Civ6_Runtime_Core_Replacement_DSM_Execution_Plan.md`；本目录只记录实际
落地的边界与迁移状态。

## 不变量

- 同一对局在任意时刻只有一个写入 owner。
- 没有新的领域 Evidence，`UNKNOWN` 不会自动升级为任何结论。
- 每个 mutation 均绑定 `game_id`、`branch_id`、`operation_id`；读档产生新 branch。
- 模型工具只能经 SessionKernel 提交 mutation，不能直接连接 FireTuner。
- Recovery、Telemetry、Context 和 UI 不能修改操作执行事实。

## 当前限制

工作树已有未跟踪的 `design/graph-idea-visual.html`，它不是本次重构产物，不能
作为“工作树干净”的验收证据，也不会被纳入本重构的提交。
