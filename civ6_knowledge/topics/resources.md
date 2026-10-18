# 资源、电力与气候

规划资源供应、电厂或沿海防护时读取；按[统一查询](../README.md#查询)展开规则与来源。

## 资源供应

揭示资源、修建适用改良、积累库存分别核对。资源类别决定能否套用库存规则；地图有资源不等于已获得供应。

- `MECHANIC_RESOURCES_SUPPLY`：资源揭示、改良前置与战略库存。
- `MECHANIC_RESOURCES_CONSUMPTION`：单位开工／维护和项目资源费。

例：`RESOURCE_COAL`；证据看 `Resources`、`Improvement_ValidResources`、`Resource_Consumption`。单位持续消耗另见 `Units_XP2`。

## 电力与排放

`MECHANIC_RESOURCES_POWER` 关联燃料、电厂和供电收益；先区分 `RequiredPower`、`PowerProvided` 与 `CO2perkWh`。区域覆盖、实际需求、燃料及设施状态决定本局供电，核电另有反应堆标记；太阳能和水电另有免费电力定义，仍需核对设施前置与生效情况。

建筑、电厂和供电加成看 `Buildings_XP2`、`Building_YieldChangesBonusWithPower`；不要将基础燃料参数直接当作本回合总排放。

## 灾害与防护

- `MECHANIC_RESOURCES_FLOODS`：河流洪水的损害、可能肥力收益与强度设置。
- `MECHANIC_RESOURCES_SEA_LEVEL`：气候阶段、沿海低地与后期肥力变化。
- `MECHANIC_RESOURCES_BARRIER`：防洪堤的科技、城市和动态成本条件。

例：`RANDOM_EVENT_FLOOD_MAJOR`。损害、收益、频率分别查 `RandomEvent_Damages`、`RandomEvent_Yields`、`RandomEvent_Frequencies`。沿海防洪标记不等于河流防洪；不据静态参数预测下一次灾害或剩余回合。

## 本局核对与关联

`get_empire_resources` 查库存和收支；`get_builder_tasks` 查改良候选；`get_cities`、`get_city_production` 查城市与生产；`get_map_area`、`get_notifications` 补地图和事件。工具未返回电力、气候或低地状态时保留缺口，不以定义代替。

解锁查[科技](technologies.md)或[市政](civics.md)，设施落位查[区域](districts.md)，经营收益查[经济](economy.md)，单位消耗查[战斗](combat.md)，议会修正查[世界议会](congress.md)。
