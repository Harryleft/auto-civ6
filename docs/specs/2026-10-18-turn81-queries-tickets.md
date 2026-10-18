# 第 61～81 回合查询与展示工作项

规格：[查询与展示](2026-10-18-turn81-queries.md)。本地 Markdown tracker，标签 ready-for-agent；集成分支 codex/decision-repair-turn41。

| 工作项 | 优先级／依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| Q1 商路候选 | P2／无，首先合入 | 经济 Lua、商路公开查询及必要模型／叙述、对应测试 | ready-for-agent |
| Q2 资源可见性 | P2／无，Q1 后集成 | 城市和建造者 Lua、共享资源解释及对应测试 | ready-for-agent |
| Q3 通知详情 | P3／无，Q2 后集成 | 通知 Lua、模型、列表和回合叙述、对应测试 | ready-for-agent |
| Q4 离线集成 | Q1、Q2、Q3 | 独立 merger、完整测试、双轴审查 | blocked |
| Q5 T61→81 验收 | Q4 | 父任务：手动决策、保存恢复、保护存档和验收 | blocked |

实施者使用独立工作树和分支，交付前合入集成分支最新提交；独立 merger 按优先级合入。完整离线通过后连接游戏。结束清理工作树，不 push。
