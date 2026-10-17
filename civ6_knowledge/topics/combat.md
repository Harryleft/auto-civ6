# 战斗与移动：路径、攻击和编队

选择行军路线、攻击目标、晋升或升级时读取。按[统一查询命令](../README.md#查询)展开单条规则。

## 路径与控制区

`MECHANIC_COMBAT_MOVEMENT` 对照单位 `BaseMoves`、地形 `MovementCost`、地貌 `MovementChange` 及道路定义。基础移动力不能直接换算为本回合可走格数；河流、道路、上下船和能力会改变结果。

`MECHANIC_COMBAT_CONTROL_ZONE` 区分施加与忽略控制区：远程／轰炸类通常不施加，骑兵／海军袭击类可忽略，河流阻隔控制区。被截停后若移动力满足要求仍可攻击；实际路径以寻路核对。

## 攻击与攻城

`MECHANIC_COMBAT_ATTACK` 区分 `Combat`、`RangedCombat`、`Bombard`、射程和视线；晋升可能改变攻击次数或移动后攻击限制。基础战力与全局参数不足以推算本次伤害。

`MECHANIC_COMBAT_SIEGE` 将城墙层级、近战／抗骑兵单位和支援单位条件一起核对。破城槌对中世纪及之后城防无效，攻城塔对文艺复兴及之后城防无效。围城须核对全部相邻格的占据、控制与不可通行状态，成立后停止自然回血。

## 升级、晋升与编队

- `MECHANIC_COMBAT_UPGRADE`：升级链给出候选目标；研究、资源、金币、位置及单位状态仍决定能否升级。检查目标单位，不按旧单位的资源需求估算。
- `MECHANIC_COMBAT_FORMATIONS`：区分护送编队与兵团／军队、舰队／无敌舰队。市政解锁、相同单位、编队资格和当前规模共同约束合并；合并不可拆分，保留经验最高单位的晋升，生命上限不增加。
- 晋升按 `UnitPromotionPrereqs` 与当前可选项核对；并列前置不是可以直接套用的全部必需条件。

## 本局核对

| 问题 | 已有入口 |
| --- | --- |
| 位置、剩余移动、生命与单位 ID | `get_units` |
| 地形、可见敌军与目标位置 | `get_map_area` |
| 可达性、路径与预计回合 | `get_pathing_estimate` |
| 当前可选晋升 | `get_unit_promotions` |

现有查询不提供通用战斗伤害预览或全部编队合法候选；资料无法补出这些当前事实。攻击、升级与合并前保留这个能力边界。

## 继续查阅

示例 ID：`UNIT_WARRIOR`、`TERRAIN_GRASS_HILLS`、`FEATURE_FOREST`、`PROMOTION_COMMANDO`。研究前置查[科技](technologies.md)／[市政](civics.md)，消耗查[资源与电力](resources.md)，战争关系查[外交与贸易](diplomacy.md)。

证据使用单位、能力／修正器、晋升、地形／地貌、道路、升级链、全局参数与百科概念；数据关联不等于当前单位已获得相应能力。
