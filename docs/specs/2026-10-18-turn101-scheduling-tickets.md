# T81→101 调度修复工作项

规格：[调度与等待识别](2026-10-18-turn101-scheduling.md)。本地 tracker，标签 ready-for-agent；沿用集成分支 codex/decision-repair-turn41，审查基点 b9edc59。

| 工作项 | 优先级／依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| S1 原生外交关闭与等待阶段 | P2／无，首先合入 | end_turn、GameState／server 的阶段与清理入口、外交与结束回合 Lua、必要连接上下文发现、对应测试 | ready-for-agent |
| S2 移动当回合可执行性 | P2／无，S1 后集成 | units 移动 Lua、必要动作确认、对应测试 | ready-for-agent |
| S3 城市资源提示 | P3／无，S2 后集成 | cities Lua、narrate 城市提示、对应测试 | ready-for-agent |
| S4 离线集成与独立审查 | S1、S2、S3 | 独立 merger、完整测试、Standards／Spec 复查 | blocked |
| S5 同轮诊断与 T81→101 | S4 | 父任务：T78 受控对照、手动决策、净20回合、保存恢复、保护成果及验收 | blocked |

各实施者从规格提交建立独立工作树，使用 tdd；完成前合入集成分支最新提交。独立 merger 按 S1→S2→S3 合入。沿用已确认测试边界，不连接游戏。离线与审查完成后再实机，最终清理工作树，不 push。
