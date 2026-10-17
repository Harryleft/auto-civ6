# 四项架构深化工作项

规格：[四项架构深化](2026-10-17-architecture-deepening.md)。本地 Markdown tracker；初始标签 ready-for-agent；固定基点 ce1e843；集成分支 codex/architecture-deepening。任务完成以单项验收、提交和集成记录标记 done，不创建远程 Issue／PR。

| 工作项 | 依赖 | 责任范围 | 状态 |
| --- | --- | --- | --- |
| A1 动作提交与确认 | 无 | 集中动作生命周期，四类动作复用，关键 Lua listener 行为验证 | done；4c5004d，合入d5beea2；917全量通过 |
| A2 存档替换所有权 | 无 | GameState 拥有完整替换、typed 原生结果、MCP 只调度、状态清理 | done；2aa4272，合入1ca84ad；913全量通过 |
| A3 议会事实解释 | 无 | 共享阶段与可投事实，查询明确语义，Lua 行为回归 | done；1d6238e，合入25884a4；146 focused通过 |
| A4 回合推进所有权 | 无 | 自持推进状态、消除跨文件私有字段约定、真实 GameState 测试 | done；9603149，合入0dd6331；923全量通过 |
| A5 集成验证与独立审查 | A1、A2、A3、A4 | 共享状态一致性、全量离线、双轴审查及修正、文档和保护校验 | in-progress；已集成923通过，公共事实收口与独立审查中 |

A1／A2／A4 虽涉及同一 GameState 文件，但修改区域不同：A1 负责动作状态、提交与确认；A2 负责保存／加载生命周期；A4 负责推进状态与推进报告协作。每项独立 worktree，合入前同步集成 tip；不得覆盖其他工作项。并发受可用 agent 槽位限制，先启动三个，完成者腾出槽位启动第四项。父任务统一调度共享文档与 tracker。

测试沿用用户已确认的 GameState／FakeConn、Lua 5.1、内存流和临时目录 seam。完成后清理实施 worktree，保留集成分支；不 push、不操作真实游戏。
