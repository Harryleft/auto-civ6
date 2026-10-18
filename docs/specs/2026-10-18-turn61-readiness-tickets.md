# 第 41～61 回合修复工作项

规格：[跳过、待命与事实叙述](2026-10-18-turn61-readiness.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 `codex/decision-repair-turn41`。

| 工作项 | 优先级／依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| R1 原生 skip 与当前待命 | P2／无 | 单位 Lua、GameState 单位方法、动作确认必要接口、单位公开回归 | ready-for-agent |
| R2 事实叙述 | P2／无；集成在 R1 后 | 概览模型／Lua／叙述、GameState 差分单位消失文案、对应公开回归 | ready-for-agent |
| R3 集成与离线验收 | R1、R2 | 独立 merger、双轴审查、完整离线验证 | blocked |
| R4 T41→61 实机与成果保护 | R3 | 父任务：逐步决策、过程／结果档、恢复比对和验收记录 | blocked |

R1、R2 在独立工作树按 tdd 实施，共享文件仅修改各自职责的代码段，不覆盖其他改动；交付前合入集成分支最新提交。由独立 merger 合入，完整离线通过再进入实机。任务结束清理实施工作树，保留分支、存档及验收；不 push。
