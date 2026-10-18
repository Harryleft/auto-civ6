# 获胜策略工作项

规格：[获胜导向策略与预警](2026-10-18-win-strategy.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| S1 胜利事实与确认后只读预警 | 无 | 单一实施任务：胜利 Lua／模型／叙述、GameState 报告、工具说明和真实回归 | completed |
| S2 获胜决策指南与入口 | 无 | 独立文档任务：Agent 策略、AGENTS 路由、操作／导航的必要链接 | completed `42f21ff` / merge `9867da9` |
| S3 离线集成与双轴审查 | S1、S2 | 父任务：独立 merger、全量离线、Standards／Spec 审查及问题关闭 | ready-for-agent |
| S4 原始地图获胜实测 | S3 | 父任务独占实机：原始指纹、早期扩张、路线与预警、原生终局、存档保护与验收 | deferred-by-user：先不进入下一轮测试 |

实施采用独立 worktree／tdd，文档仅必要检查；当前模型实际决策，Runtime 不代替模型玩游戏。不得操作旧存档或 push。

用户最新指令：先不进入下一轮测试。S1／S3 继续离线实施及审查；S4 暂缓，不加载基准、不连接或操作游戏。
