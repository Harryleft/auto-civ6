# 经济与城市成长

人口停滞、住房不足、宜居度变化或比较建筑与政策时读取。

## 判断入口

| 问题 | 规则卡 |
| --- | --- |
| 食物产出为何没有转成人口 | `MECHANIC_ECONOMY_GROWTH` |
| 住房余量如何限制增长 | `MECHANIC_ECONOMY_HOUSING` |
| 宜居需求与来源如何核对 | `MECHANIC_ECONOMY_AMENITIES` |
| 建筑成本、产出与维护费如何比较 | `MECHANIC_ECONOMY_PRODUCTION` |

## 本局核对

- `get_cities` 读取人口、食物结余、增长、住房、宜居度及城市产出；以实际返回字段为准。
- `get_city_production` 核对当前可建项和费用；`get_policies` 核对现任政体、槽位和政策。
- 建筑的基础产出、住房、维护费和前置分开比较；地块工作分配、供电及间接效果仍需核对本局。
- 人口、住房和宜居度是不同约束；增长受阻时分别查证，不能把提高食物视为通用解法。

## 继续查阅

按[统一查询命令](../README.md#查询)展开单张规则卡。建筑示例：`BUILDING_GRANARY`；产出示例：`YIELD_FOOD`。

解锁条件读[科技](technologies.md)和[市政](civics.md)，建筑所在区域与落位读[区域建设](districts.md)。

证据表：`GlobalParameters`、`Buildings`、`Building_YieldChanges`、`Happinesses`、`CivilopediaTexts`、`Policies`。住房阈值与增长参数可直接核对。幸福等级数值以 `Happinesses` 为准；概念正文部分收益加成已滞后，规则卡并列保留差异。间接 Modifier 不求值。
