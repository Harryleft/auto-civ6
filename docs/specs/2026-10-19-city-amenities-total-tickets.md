# 城市宜居度 tracker

[规格](2026-10-19-city-amenities-total.md)，ready-for-agent。

| ID | 工作项 | 依赖 | 状态 | 验收 |
| --- | --- | --- | --- | --- |
| A1 | 修正查询并补执行 Lua 回归 | 无 | complete | 原生总量准确、纯读、公共解析契约不变 |
| A2 | 独立合入、相关验证和双轴审查 | A1 | complete | 无遗留发现，保留原 dirty tracker |
| A3 | 同轮实机总量对照 | A2 | complete | 对照原生总量、需求，存档保全 |

父任务负责文档及游戏；实施者仅查询与测试，独立WT，不实机、不push、不撤销他人改动。

A1：eea45ac、944fe72；A2：独立合入7d17f71，两个相关模块21项通过，编译与差分通过；Standards／Spec各0材料性问题。A3：1763–1764同轮T206附着，无游戏重载，首都总量9、基什5，与1754原生值一致；四处农田已恢复，原始证据保全。见[验收](../acceptance/2026-10-19-city-amenities-total.md)。
