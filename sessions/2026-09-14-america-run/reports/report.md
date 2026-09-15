# auto-civ6 地图数据解析报告

对局 **`ai-civ6-map-01`**（游戏身份 `sumeria_-1162067840`），我方 **苏美尔**，快照于 **第 141 回合**。

- 地图尺寸：**60 × 38** = 2280 格
- 陆地 909 格 / 水域 1371 格（陆地占比 39.9%）
- 河流经过 272 格，丘陵 242 格，山脉 60 格，沿海 220 格
- 玩家 11 名，城市 37 座
- 回合快照覆盖第 2–141 回合，共 138 回合

## 玩家

| pid | 文明 | 类型 | 领土格数 |
|---|---|---|---|
| 0 | 苏美尔 | 我方 | 112 |
| 1 | 加拿大 | 主要文明 | 152 |
| 2 | 巴西 | 主要文明 | 157 |
| 3 | 瑞典 | 主要文明 | 159 |
| 4 | 卡霍基亚 | 城邦（Trade） | 13 |
| 5 | 库马西 | 城邦（Cultural） | 12 |
| 6 | 桑给巴尔 | 城邦（Trade） | 14 |
| 7 | 巴比伦 | 城邦（Scientific） | 12 |
| 8 | 拉帕努伊 | 城邦（Cultural） | 15 |
| 9 | 墨西哥城 | 城邦（Industrial） | 10 |
| 62 | 自由城市 | 主要文明 | 0 |

## 地形分布

| 地形 | ID | 格数 | 占比 |
|---|---:|---:|---:|
| 远洋（OCEAN） | 16 | 969 | 42.5% |
| 浅海（COAST） | 15 | 402 | 17.6% |
| 平原（PLAINS） | 3 | 264 | 11.6% |
| 草原（GRASS） | 0 | 228 | 10.0% |
| 平原丘陵（PLAINS_HILLS） | 4 | 120 | 5.3% |
| 草原丘陵（GRASS_HILLS） | 1 | 79 | 3.5% |
| 沙漠（DESERT） | 6 | 68 | 3.0% |
| 苔原（TUNDRA） | 9 | 31 | 1.4% |
| 沙漠丘陵（DESERT_HILLS） | 7 | 28 | 1.2% |
| 草原山脉（GRASS_MOUNTAIN） | 2 | 24 | 1.1% |
| 平原山脉（PLAINS_MOUNTAIN） | 5 | 22 | 1.0% |
| 雪原（SNOW） | 12 | 16 | 0.7% |
| 沙漠山脉（DESERT_MOUNTAIN） | 8 | 12 | 0.5% |
| 苔原丘陵（TUNDRA_HILLS） | 10 | 11 | 0.5% |
| 雪原丘陵（SNOW_HILLS） | 13 | 4 | 0.2% |
| 雪原山脉（SNOW_MOUNTAIN） | 14 | 1 | 0.0% |
| 苔原山脉（TUNDRA_MOUNTAIN） | 11 | 1 | 0.0% |

## 地貌（Feature）分布

> **重要**：仓库 `web/src/lib/terrain-colors.ts` 的 `FEATURE_OVERLAY_COLORS` 把 0/1/2/3 标成了 FOREST/JUNGLE/MARSH/OASIS，**与游戏实际数据不符**。下表按游戏数据库加载顺序重建，依据见文末「地貌 ID 映射勘误」。

| ID | 地貌（勘误后） | 格数 | 其中带河流 | 主要分布地形 |
|---:|---|---:|---:|---|
| 1 | 海冰（FEATURE_ICE） | 526 | 0 | OCEAN 474、COAST 52 |
| 3 | 森林（FEATURE_FOREST） | 99 | 16 | PLAINS 33、GRASS 29、GRASS_HILLS 18 |
| 2 | 雨林（FEATURE_JUNGLE） | 87 | 22 | PLAINS 61、PLAINS_HILLS 26 |
| 19 | 泛滥平原（草原）（FEATURE_FLOODPLAINS_GRASSLAND） | 46 | 46 | GRASS 46 |
| 18 | 珊瑚礁（FEATURE_REEF） | 24 | 0 | COAST 24 |
| 20 | 泛滥平原（平原）（FEATURE_FLOODPLAINS_PLAINS） | 19 | 19 | PLAINS 19 |
| 5 | 沼泽（FEATURE_MARSH） | 12 | 0 | GRASS 12 |
| 23 | 火山土（FEATURE_VOLCANIC_SOIL） | 12 | 1 | PLAINS_HILLS 4、PLAINS 3、GRASS 2 |
| 22 | 火山（FEATURE_VOLCANO） | 5 | 1 | GRASS_MOUNTAIN 3、DESERT_MOUNTAIN 2 |
| 0 | 泛滥平原（沙漠）（FEATURE_FLOODPLAINS） | 5 | 5 | DESERT 5 |
| 21 | 地热裂缝（FEATURE_GEOTHERMAL_FISSURE） | 4 | 2 | DESERT_HILLS 2、PLAINS_HILLS 1、DESERT 1 |
| 4 | 绿洲（FEATURE_OASIS） | 2 | 0 | DESERT 2 |
| 28 | 棉花堡（FEATURE_PAMUKKALE） | 2 | 0 | GRASS 2 |
| 29 | 维苏威火山（FEATURE_VESUVIUS） | 1 | 0 | PLAINS 1 |
| 16 | 黥基·德·贝马拉哈（FEATURE_TSINGY） | 1 | 0 | PLAINS 1 |
| -1 | 无地貌 | 1435 | — | — |

## 资源分布

共 27 种不同资源 ID，占地 305 格。

> 仓库内没有任何 **资源 ID → 名称** 的映射表（游戏侧按 `RESOURCE_*` 字符串处理），本机也未找到可用于还原索引序的游戏数据库，故此处只报 ID。

| 资源 ID | 格数 | 主要分布地形 |
|---:|---:|---|
| 5 | 37 | COAST 37 |
| 3 | 26 | COAST 26 |
| 47 | 24 | PLAINS 8、GRASS 6、DESERT 4 |
| 9 | 20 | PLAINS 19、DESERT 1 |
| 51 | 19 | COAST 19 |
| 8 | 17 | GRASS 15、GRASS_HILLS 2 |
| 1 | 14 | GRASS 14 |
| 6 | 13 | GRASS 13 |
| 45 | 12 | COAST 6、SNOW 2、TUNDRA 2 |
| 42 | 10 | PLAINS 7、GRASS 3 |
| 2 | 8 | GRASS_HILLS 5、SNOW_HILLS 1、TUNDRA_HILLS 1 |
| 4 | 8 | GRASS 3、TUNDRA 2、TUNDRA_HILLS 1 |
| 44 | 8 | GRASS 5、TUNDRA 1、DESERT 1 |
| 43 | 8 | PLAINS_HILLS 5、DESERT_HILLS 3 |
| 41 | 8 | PLAINS_HILLS 3、GRASS_HILLS 2、GRASS 2 |
| 0 | 8 | PLAINS 6、PLAINS_HILLS 2 |
| 7 | 7 | DESERT_HILLS 3、GRASS_HILLS 2、PLAINS_HILLS 2 |
| 30 | 6 | PLAINS 4、TUNDRA_HILLS 1、GRASS 1 |
| 22 | 6 | PLAINS 6 |
| 17 | 6 | PLAINS 5、PLAINS_HILLS 1 |
| 40 | 6 | PLAINS 4、DESERT 2 |
| 15 | 6 | PLAINS_HILLS 4、PLAINS 2 |
| 20 | 6 | GRASS 3、PLAINS 3 |
| 14 | 6 | PLAINS_HILLS 3、PLAINS 2、GRASS_HILLS 1 |
| 11 | 6 | PLAINS 3、PLAINS_HILLS 3 |
| 21 | 6 | GRASS_HILLS 4、GRASS 1、PLAINS_HILLS 1 |
| 46 | 4 | GRASS_HILLS 1、GRASS 1、PLAINS_HILLS 1 |

## 道路

| route ID | 含义 | 格数 |
|---|---|---:|
| -1 | 无道路 | 2075 |
| 3 | 铁路 | 118 |
| 2 | 工业铁路 | 82 |
| 1 | 古典道路 | 5 |

## 城市（快照时点）

| 城市 | 坐标 (x,y) | 归属 | 人口 |
|---|---|---|---:|
| 基什 | (24, 18) | 苏美尔 | 12 |
| 尼普尔 | (20, 17) | 苏美尔 | 10 |
| 乌鲁克 | (14, 17) | 苏美尔 | 9 |
| 西帕尔 | (18, 20) | 苏美尔 | 9 |
| 乌尔 | (27, 16) | 苏美尔 | 6 |
| 埃利都 | (11, 18) | 苏美尔 | 6 |
| 阿达卜 | (19, 13) | 苏美尔 | 6 |
| 拉拉克 | (13, 21) | 苏美尔 | 3 |
| OTTAWA | (44, 7) | 加拿大 | 11 |
| QUEBEC_CITY | (47, 9) | 加拿大 | 8 |
| HAMILTON | (41, 6) | 加拿大 | 7 |
| TORONTO | (46, 13) | 加拿大 | 7 |
| HALIFAX | (50, 21) | 加拿大 | 7 |
| SAINT_JOHN | (50, 17) | 加拿大 | 6 |
| MONTREAL | (50, 12) | 加拿大 | 3 |
| KINGSTON | (53, 14) | 加拿大 | 2 |
| RIO_DE_JANEIRO | (38, 19) | 巴西 | 15 |
| MANAUS | (38, 15) | 巴西 | 15 |
| FORTALEZA | (41, 21) | 巴西 | 12 |
| NATAL | (45, 18) | 巴西 | 10 |
| SALVADOR | (34, 19) | 巴西 | 8 |
| PORTO_ALEGRE | (42, 16) | 巴西 | 3 |
| SAO_PAULO | (31, 17) | 巴西 | 2 |
| UPPSALA | (29, 25) | 瑞典 | 10 |
| STOCKHOLM | (32, 30) | 瑞典 | 7 |
| GOTEBORG | (28, 31) | 瑞典 | 7 |
| NORRKOPING | (35, 28) | 瑞典 | 6 |
| LUND | (26, 23) | 瑞典 | 5 |
| UMEA | (23, 24) | 瑞典 | 5 |
| VASTERAS | (24, 31) | 瑞典 | 3 |
| JONKOPING | (19, 24) | 瑞典 | 2 |
| CAHOKIA | (46, 23) | 卡霍基亚 | 10 |
| KUMASI | (35, 11) | 库马西 | 8 |
| ZANZIBAR | (23, 13) | 桑给巴尔 | 12 |
| BABYLON | (25, 27) | 巴比伦 | 7 |
| RAPA_NUI | (29, 20) | 拉帕努伊 | 8 |
| MEXICO_CITY | (33, 23) | 墨西哥城 | 8 |

我方（苏美尔）共 8 座城市，合计人口 61，平均 7.6。

## 回合时间线（城市数量变化）

从第 2 回合的 9 座城市，到第 141 回合的 37 座城市。

| 回合 | 城市总数 |
|---:|---:|
| 2 | 9 |
| 10 | 12 |
| 20 | 13 |
| 30 | 15 |
| 40 | 19 |
| 50 | 21 |
| 60 | 22 |
| 70 | 23 |
| 80 | 26 |
| 90 | 28 |
| 100 | 29 |
| 110 | 32 |
| 120 | 33 |
| 130 | 35 |
| 140 | 37 |
| 141 | 37 |

## 地貌 ID 映射勘误

仓库 `web/src/lib/terrain-colors.ts` 中声明的映射是：

```text
0=FEATURE_FOREST  1=FEATURE_JUNGLE  2=FEATURE_MARSH  3=FEATURE_OASIS
4=FEATURE_FLOODPLAINS  5=FLOODPLAINS_GRASSLAND  6=FLOODPLAINS_PLAINS
```

本机游戏数据（Gathering Storm 已装）显示的真实加载顺序是：

```text
Base/Assets/Gameplay/Data/Features.xml                   → 索引 0–17
DLC/Expansion2/Data/Expansion1_Features.xml  (R&F 新增)  → 索引 18（仅 FEATURE_REEF）
DLC/Expansion2/Data/Expansion2_Features.xml  (GS 新增)   → 索引 19–30
```

即 `0=FEATURE_FLOODPLAINS`、`1=FEATURE_ICE`、`2=FEATURE_JUNGLE`、`3=FEATURE_FOREST`、`4=FEATURE_OASIS`、`5=FEATURE_MARSH`、`6=FEATURE_BARRIER_REEF`。

四条独立证据支持勘误后的映射（不是猜测）：

1. 索引 **1** 共 526 格，其中 **474 格落在远洋、52 格落在浅海** —— 只可能是海冰，不可能是仓库所说的雨林。
2. 索引 **19** 共 46 格，**全部为草原且 46/46 带河流** —— 正是草原泛滥平原的生成条件。
3. 索引 **20** 共 19 格，**全部为平原且 19/19 带河流** —— 正是平原泛滥平原的生成条件。
4. 索引 **18** 共 24 格，**全部落在浅海** —— 与 R&F 唯一新增的 `FEATURE_REEF` 一致。

影响：`strategic-map.tsx` 用 `FEATURE_OVERLAY_COLORS` 给地貌叠色，所以网页版战略地图会把 **海冰画成绿色植被**、把森林/绿洲/沼泽全部画错颜色。此外 `getFeatureOverlay()` 只覆盖 ID 0–6，索引 ≥7 的地貌（含全部自然奇观、火山、地热裂缝、珊瑚礁、泛滥平原）在网页上**完全没有叠加显示**。

## 数据来源与已知限制

- 目录由 MCP 的 `src/civ_mcp/map_capture.py` 在游戏过程中 dump 到 `~/.civ6-mcp/`，再由仓库作者合并进 `map/`。
- 本机 `~/.civ6-mcp/` **不存在**，说明这些数据是在**另一台机器（macOS）**上采集的，随仓库分发；对应的存档 `0_MCP_0141.Civ6Save` 未包含在仓库里。
- `map/README.md` 记录：第 141 回合（世界议会回合）在原采集机上遇到引擎级卡死，因此快照停在该回合。
- 资源 ID 缺少 ID→名称 的权威映射（游戏侧按 `RESOURCE_*` 字符串处理，仓库里没有 ID 表），报告只做 ID 级呈现；地貌 ID 已按游戏数据库加载顺序重建，见上一节。
