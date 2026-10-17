# 胜利与宗教

选择长期目标、比较胜利进度、选择信条或安排传播时读取。

## 判断入口

| 问题 | 规则卡 |
| --- | --- |
| 太空项目和系外行星进度 | `MECHANIC_VICTORY_SCIENCE` |
| 游客与文化胜利条件 | `MECHANIC_VICTORY_CULTURE` |
| 原始首都与征服胜利 | `MECHANIC_VICTORY_DOMINATION` |
| 外交胜利点与议会 | `MECHANIC_VICTORY_DIPLOMATIC` |
| 主流宗教与宗教胜利 | `MECHANIC_VICTORY_RELIGIOUS` |
| 信条类别、效果与传播 | `MECHANIC_VICTORY_BELIEFS_SPREAD` |

## 本局核对

- `get_victory_progress` 核对启用的胜利、竞争者与实际进度；分数领先不等于已满足其他胜利。
- `get_city_production`、`get_tech_civics` 核对太空项目候选与研究；基础项目成本不代表当前剩余回合。
- `get_religion_spread` 读取城市宗教分布；`get_religion_beliefs` 与 `get_pantheon_beliefs` 核对阶段与可选信条。
- 宗教单位传播的宗教取决于其归属；不能把玩家创建的宗教视为所有单位的默认宗教。
- 当前需求集合优先于概念正文：旧科学胜利正文仍写火星终点，当前规则要求系外行星阶段。

## 继续查阅

按[统一查询命令](../README.md#查询)展开。实体示例：`VICTORY_TECHNOLOGY`、`BELIEF_RELIGIOUS_COLONIZATION`、`RELIGION_PANTHEON`。

科技与项目解锁读[科技](technologies.md)，宇航中心落位读[区域建设](districts.md)，投票阶段读[世界议会](congress.md)。

证据表：`Victories`、需求集合与需求参数、`Projects`、`GlobalParameters`、`BeliefClasses`、`BeliefModifiers`、`CivilopediaTexts`。引擎需求类型保留原名；静态资料不执行胜利判定或完整传播计算。
