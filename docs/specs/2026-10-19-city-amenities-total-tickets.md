# 城市宜居度 tracker

[规格](2026-10-19-city-amenities-total.md)，ready-for-agent。

| ID | 工作项 | 依赖 | 状态 | 验收 |
| --- | --- | --- | --- | --- |
| A1 | 修正查询并补执行 Lua 回归 | 无 | ready | 原生总量准确、纯读、公共解析契约不变 |
| A2 | 独立合入、相关验证和双轴审查 | A1 | blocked | 无遗留发现，保留原 dirty tracker |
| A3 | 同轮实机总量对照 | A2 | blocked | 对照原生总量、需求，存档保全 |

父任务负责文档及游戏；实施者仅查询与测试，独立WT，不实机、不push、不撤销他人改动。
