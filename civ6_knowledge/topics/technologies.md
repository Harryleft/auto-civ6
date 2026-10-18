# 科技：前置、尤里卡与解锁

规划科研路线或解释科技解锁时读取。

## 规则

- `TechnologyPrereqs` 中同一科技的多条前置，在普通静态树中共同核对；随机树的实际前置需查询本局。
- `Boosts` 保存尤里卡的对象、数量和需求条件。原始 `Boost` 值需结合规则与能力修正，不能直接当作最终完成比例。
- `PrereqTech` 提取直接解锁。单位仍需核对专属／替代和战略资源；建筑、项目仍需核对区域、先行建筑及城市条件；区域还需合法落位和容量。
- 资源关联须区分揭示、开采与改良前置；路线关联也需读取原始条件，不能统称为已获得或可建设。

## 本局核对

| 事实 | 查询工具 |
| --- | --- |
| 研究、前置、费用、进度、尤里卡与展示候选 | `get_tech_civics` |
| 解锁对象是否可建 | `get_city_production` |
| 资源与可见地块 | `get_empire_resources`、`get_map_area` |

## 继续查阅

示例 ID：`TECH_WRITING`，按[统一查询命令](../README.md#查询)展开。写作解锁学院后，落位查[区域建设](districts.md)；市政条件查[市政](civics.md)。

证据表：`Technologies`、`TechnologyPrereqs`、`Boosts` 及解锁对象表；原生片段 `technology-live-prerequisites` 区分静态与活动随机前置。
