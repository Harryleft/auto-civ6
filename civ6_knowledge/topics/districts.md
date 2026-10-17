# 区域建设：合法位置、容量与收益

准备建设或比较区域落位时读本页。先读 [区域规则卡](../rules/districts.json) 的前置和条件，再查询本局城市与地块；相邻收益高的地块未必能够建设。

## 将条件一起核对

| 条件 | 离线资料提供什么 | 本局必须补充什么 |
| --- | --- | --- |
| 科技／市政前置 | `PrereqTech`、`PrereqCivic` 关联 | 已完成研究与当前可建候选 |
| 人口与数量 | `RequiresPopulation`、`OnePerCity`、`MaxPerPlayer` 等字段 | 当前人口、已建／已放置区域、容量修正；不同区域不能套用同一人口公式 |
| 地形与地貌 | 合法地形、必需地貌及扩展条件 | 可见地块的地形、地貌、资源、所有权和已有对象 |
| 河流、海岸与连通 | `Coast`、`AdjacentToLand`、`Aqueduct`、`Canal`、`OnePerRiver` 等定义 | 河流具体边、海岸、城市及水体关系；水坝／运河等特殊落位以引擎候选确认 |
| 奇观、资源与邻接城市 | 相邻规则及 `NoAdjacentCity` 等限制 | 自然奇观与世界奇观类型、可移除对象和实际位置；相邻收益条件不等于落位许可 |
| 文明特性与替代 | `TraitType`、替代和特性关联 | 本局文明／领袖是否拥有特性及活动内容 |
| 成本 | `Cost`、`CostProgressionModel`、维护费等源字段 | 本局实际生产／购买费用、速度、进度与相关修正 |

`RequiresPopulation` 表示是否受人口容量机制约束，不直接给出本局可用名额。基础成本和成本递增参数也不是最终费用。特殊区域、唯一性、替代关系与模式修正，应随主规则一起读取；未覆盖的引擎条件由实时合法候选补齐。

## 相邻收益是条件集合

`District_Adjacencies` 指向 `Adjacency_YieldChanges`。每项要一起读取产出类型、`YieldChange`、`TilesRequired`、相邻对象、科技／市政前置及过时条件，保留相邻区域、改良、资源、地形、河流、奇观等对象的区别。

河流加成不能简单按相邻河边数量重复累加；原生百科对河流收益有单次计算的说明。政策、文明特性、建筑与其他 Modifier 还可能改变最终产出，本期没有完整求值。比较工业区等复杂区域时，先读完整关联定义，再用本局顾问输出检查候选与估算。

解释：落位还应考虑损失的地块产出、未来区域和建设时机。资料卡描述规则；这些机会成本属于策略判断，需要当前地图事实。

## 与当前游戏连接

1. 使用 `get_cities` 确认城市 ID、人口与状态，`get_city_production` 核对当前可建对象及费用。
2. 使用 `get_district_advisor(city_id, district_type)` 读取合法落位及相邻收益建议；需要地块上下文时读 `get_map_area`，需要购地时读 `get_purchasable_tiles`。
3. 选择后由 `set_city_production` 携带明确坐标提交，再核对动作结果和落位事实。离线坐标、相邻公式或旧顾问输出不能替代当前合法性检查。

前置路线分别见 [科技](technologies.md) 和 [市政](civics.md)。世界议会效果若改变建设成本或收益，另查 [世界议会](congress.md) 与本局生效结果。

## 需要深查时

```sh
python3 query.py show DISTRICT_INDUSTRIAL_ZONE --level rule
python3 query.py show DISTRICT_INDUSTRIAL_ZONE --level evidence
```

命令从知识目录执行。原始来源见 [快照](../sources/snapshot.json) 的 `Districts`、`Districts_XP2`、`District_ValidTerrains`、`District_RequiredFeatures`、`District_Adjacencies`、`Adjacency_YieldChanges` 和特性／替代表，以及 `code_evidence` 的 `district-adjacency-calculation`。原始行保留条件，关联覆盖标记区分独立规则卡和外部对象；完整落位算法与 Modifier 效果属于未覆盖机制。
