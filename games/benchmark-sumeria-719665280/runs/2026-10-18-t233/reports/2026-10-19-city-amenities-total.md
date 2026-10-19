# 城市宜居度总量验收

[规格](../changes/2026-10-19-city-amenities-total.md)与[工作项](../changes/2026-10-19-city-amenities-total-tickets.md)。实施eea45ac、944fe72，独立合入7d17f71；分支codex/native-pillage，未合main、未push。

旧城市查询将需求加到原生总量上。原版CitySupport.lua526–539定义总量为GetAmenities，净值为GetAmenities减GetAmenitiesNeeded。现直接读取总量，删除错误加法及无用需求读取；模型、解析、展示字段及纯读契约不变。

| 验证 | 结果 |
| --- | --- |
| 离线 | city_amenities_total、resource_hint_visibility两模块21项通过；完整Lua5.1查询经GameState执行，覆盖零、低于／等于／高于需求、Kish5／6，零原生写入；编译及差分检查通过 |
| 独立审查 | Standards与Spec各0材料性问题 |
| 同轮实机 | 1763T206苏美尔／吉尔伽美什，未重新加载；1764首都Amenities9、基什5，与1754原生总量一致；旧查询为18、11 |
| 保全 | 1762修复前T206存档1,250,950字节；30个原生文件已归档；141原始存档备份、5068既有验收、125检查点、33项目保护文件哈希无变化 |

1754临时Lua输出中的surplus标签是调查时误写，原始数值实际为总量；以官方定义及本次对照为准，未据误值修改政策。历史存档与验收原件保持不变。证据位于~/.civ6-mcp/acceptance/20261018-turn-terminal121/下amenities-total-impl、amenities-total-merge、amenities-total-review、preservation-t206.json和live/1753–1764。
