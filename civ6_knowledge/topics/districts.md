# 区域建设：容量、落位与收益

比较区域或选择建设位置时读取。

## 落位条件

| 检查项 | 规则字段／关系 | 本局事实 |
| --- | --- | --- |
| 研究 | `PrereqTech`、`PrereqCivic` | 已完成研究及可建候选 |
| 容量与数量 | `RequiresPopulation`、`OnePerCity`、`MaxPerPlayer` | 人口、已建／已放置区域、容量修正 |
| 地形与地貌 | 合法地形、必需地貌 | 所有权、资源、已有对象及可移除性 |
| 河流、海岸与连通 | `Coast`、`AdjacentToLand`、`Aqueduct`、`Canal`、`OnePerRiver` | 河流具体边、海岸、城市和水体关系 |
| 邻接限制 | `NoAdjacentCity` 等 | 邻接城市、奇观类型与实际位置 |
| 特性与替代 | `TraitType`、替代表及特性归属 | 文明／领袖是否具备特性 |
| 成本 | `Cost`、`CostProgressionModel`、维护费 | 实际费用、速度、进度与修正 |

`RequiresPopulation` 只标记人口容量约束，不给出本局剩余名额；不同区域不能套用同一公式。水坝、运河等特殊位置以引擎候选确认。

## 相邻收益

`District_Adjacencies` 关联 `Adjacency_YieldChanges`。一起核对产出、`YieldChange`、`TilesRequired`、相邻对象、研究前置及过时条件；区分自然奇观、世界奇观、区域、改良和资源。

河流收益不能按相邻河边数量重复累加。政策、特性和建筑可能修正最终产出；相邻收益也不能证明地块可建。

## 本局核对

- `get_cities`、`get_city_production`：城市 ID、人口、生产候选及费用。
- `get_district_advisor`：合法落位和相邻收益；地图上下文用 `get_map_area`，购地候选用 `get_purchasable_tiles`。

## 继续查阅

示例 ID：`DISTRICT_INDUSTRIAL_ZONE`，按[统一查询命令](../README.md#查询)展开。前置查[科技](technologies.md)或[市政](civics.md)，议会修正查[世界议会](congress.md)。

证据包含区域基础／扩展表、地形／地貌、替代／特性与相邻定义；原生片段 `district-adjacency-calculation` 说明相邻计算。
