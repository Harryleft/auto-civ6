# 规则简报：苏美尔／吉尔伽美什 × 神级 × 快速 × 极小四叶草

- 局：`2026-10-25-four-leaf-victory` / run `20261025-1725`；受信原始基准 `fixtures/saves/benchmark_gold_tun_1.Civ6Save`（神级、快速、极小、FourLeafClover，种子 `-1896272000`）。
- 性质：**离线只读规则研究**。未启动游戏、未连接游戏、未推回合；本文件是规则层事实与候选，不替代每回合的实机回读与模型决策。
- 结论边界：静态知识只代表规则，**不保证本局已启用、已拥有、条件已满足或动作合法**；费用、资格、地形、资源、可见性一律以本局 `state/city/unit/map/diplomacy` 回读为准。

## 0. 来源与标注法

| 标签 | 来源 | 说明 |
| --- | --- | --- |
| `[A]` | `civ6_knowledge/rules/*.json`、`civ6_knowledge/topics/*.md`、`manifest.json` | 本地规则快照：`RULESET_EXPANSION_2`、游戏版本 `1.0.12.54 (951533)`、macOS bundle `1.4.6`；`source_scope=local_cached_rules_snapshot`，`current_session_verified=false` |
| `[B]` | 本机安装数据的**只读**查询：`Civ6.app/Contents/Assets/Base/Assets/Gameplay/Data/*.xml`；`~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/Cache/DebugGameplay.sqlite`、`DebugConfiguration.sqlite` | 与 `[A]` 同一安装源，`[A]` 未收录的表（难度缩放、地图尺寸、伟人上限等）由此补齐；**同样不证明本局启用** |
| `[C]` | 项目既有实机记录（`games/…` 下的证据与报告） | 本基准的历史动态观测，只作对照，不是规则保证；不证明本轮同图同状态 |
| `(推断)` | 由参数按字段语义换算 | 静态资料未给出换算表，**必须实机核对**后才可当事实使用 |

`[A]` 的规则卡 ID 均可在离线 CLI 展开（示例）：

```text
knowledge show UNIT_SUMERIAN_WAR_CART --level rule      # 战车（AI 侧卡片在 civilizations 主题）
knowledge show MECHANIC_VICTORY_RELIGIOUS --level rule # 宗教胜利
knowledge show MECHANIC_COMBAT_SIEGE --level rule      # 城墙与攻城支援
knowledge show DIPLOACTION_DECLARE_HOLY_WAR --level rule
```

## 1. 苏美尔文明能力、吉尔伽美什、战车、金字形神塔

### 1.1 文明能力「史诗任务 / Epic Quests」（`TRAIT_CIVILIZATION_FIRST_CIVILIZATION`）`[A][B]`

- 占领**蛮族哨站**时：在通常金币之外，额外获得一次**部落村庄奖励**。`[A]` 描述原文；`[B]` 修饰器 `TRAIT_BARBARIAN_CAMP_GOODY`（`IMPROVEMENT_BARBARIAN_CAMP → IMPROVEMENT_GOODY_HUT`）。
- **征用城邦单位费用降低一半**：`[B]` `TRAIT_LEVY_DISCOUNT`（`MODIFIER_PLAYER_ADJUST_LEVY_DISCOUNT_PERCENT`，`Percent=50`）。
- 对局含义：清蛮族营 = 金币 + 村庄奖励（可能给科技/信仰/人口/单位/遗物等，具体由村庄随机表决定），并直接完成「军事传统」鼓舞；配合 1.2 的联合作战能力，早期清营收益结构性好。征用折扣是神级下借城邦军队应急的可用杠杆（城邦战争/被宣战时）。

### 1.2 领袖能力「恩奇都奇遇 / Adventures of Enkidu」（`TRAIT_LEADER_ADVENTURES_ENKIDU`）`[A][B]`

- **可以向正在与其盟友交战的文明宣战，而不影响好战程度**；`[B]` `TRAIT_ADJUST_ALLIED_WAR_DISCOUNT`（`Discount=150`）。
- **进行联合作战时（与共同敌人交战），与盟友在 5 个单元格以内共享掠夺奖励与战斗经验值**；`[B]` `TRAIT_ADJUST_JOINTWAR_EXPERIENCE`（`Range=5`）、`TRAIT_ADJUST_JOINTWAR_PLUNDER`（`Multiplier=100`）。
- 注意：该能力本身不带直接战斗力加成；共享需要**共同敌人 + 5 格内盟友**这两个条件，静态资料不求值。
- 隐藏议程「恩奇都联盟」`[A][B]`：喜欢愿意建立长期联盟的文明，讨厌谴责或进攻其朋友/盟友的文明（只影响 AI 对玩家的态度，不从议程推断 AI 意图）。

### 1.3 独特单位「战车 / War-Cart」（`UNIT_SUMERIAN_WAR_CART`）`[A][B]`

| 字段 | 值 | 说明 |
| --- | --- | --- |
| 解锁 | **无科技、无市政前置**；`PrereqTech/PrereqCivic = 空` | 开局即可在城市训练；**不需要「轮子」**（轮子解锁的是通用重型战车） |
| 战略资源 | **无**（`StrategicResource` 为空） | 不消耗马/铁，纯生产力产出 |
| 生产力成本 | `55`（标准速度基准；快速 ×0.67 ≈ **37**，见 §2.4） | 对比：勇士 40、投石兵 35、重型战车 65 |
| 战斗力 | `30` 近战 | 对比：勇士 20、重型战车 28、抗骑兵矛兵 25 |
| 移动 | `BaseMoves=3`；能力「重型战车」：**若回合开始时位于沙漠/平原/草原/冻土（开阔地形），+1 移动 → 实际 4** | 描述原文「如果该单位从开阔地形开始回合，移动力为4」 |
| 其他 | `Maintenance=0`；`PromotionClass=HEAVY_CAVALRY`；标签 `CLASS_HEAVY_CHARIOT` + `CLASS_WAR_CART`；`CanCapture=1`；施加控制区 | 0 维护费适合早期持续暴兵 |
| 特殊能力 | `ABILITY_HEAVY_CHARIOT`（+1 移动，见上）、`ABILITY_IGNORE_ZOC`（**不受敌方控制区影响**） | 忽略 ZOC 使战车能绕开肉盾直插城市/后排 |
| 抗骑兵关系 | **对战时不吃抗骑兵 +10**：抗骑兵加成仅匹配对手标签 `CLASS_HEAVY_CAVALRY / CLASS_LIGHT_CAVALRY / CLASS_RANGED_CAVALRY`，战车只有 `CLASS_HEAVY_CHARIOT`/`CLASS_WAR_CART` | 与描述「对战抗骑兵单位时无减益」一致（`[B]` `ANTI_CAVALRY_OPPONENT_REQUIREMENTS`） |
| 过期/升级 | `MandatoryObsoleteTech=TECH_STIRRUPS`（马镫，标准 390）；升级链 `UNIT_SUMERIAN_WAR_CART → UNIT_KNIGHT`（骑士：战斗力 50、移动 4、成本 220、**需铁**） | 中古前必须转骑士或退役；升级要金币且目标需资源/科技 |

补充：战车可吃「机动 / Maneuver」政策（`POLICY_MANEUVER`，军事传统市政解锁）：**远古与古典时代重型/轻型骑兵生产 +50%**；政策修饰器按 `UnitPromotionClass=PROMOTION_CLASS_HEAVY_CAVALRY` 匹配，战车的晋升类正是重型骑兵 → **战车吃 +50% 产能**。`[A][B]`

### 1.4 独特改良「金字形神塔 / Ziggurat」（`IMPROVEMENT_ZIGGURAT`）`[A][B]`

- 产出：**+2 科技**（始终）；**若地块紧邻河流，再 +1 文化**（`ZIGGURAT_RIVERADJACENCY_CULTURE`，条件 `PLOT_ADJACENT_TO_RIVER`）。描述原文一致。
- 解锁：**无科技/市政前置**（`Improvements` 行 `PrereqTech/PrereqCivic` 为空；描述「Unlocks the Builder ability…」）→ 建造者开局即可造（前提：地块归属、平坦、可改良）。
- 可建地形：平坦 **沙漠/草原/平原/冻土/雪地**；可建在 **泛滥平原**（`FEATURE_FLOODPLAINS` 及其草原/平原变体）；**不能建在丘陵**（有效地形表不含丘陵变体，与描述一致）；**无资源前置**。
- 其他：`TilesRequired=1`、可被掠夺（`PlunderAmount=50` 金币）、可被占领；改良不提供住房/食物，是早期科研型改良。
- 对局含义：苏美尔不必为科技改良抢前置，**开局建造者即可在河边平地上铺科技**（配合河流起始偏好，见 §6）。神级产出差距要靠这类“免前置”改良与击杀/占领收益补。

## 2. 神级 AI 开局加成 与 快速速度影响

### 2.1 起始单位与城市（`[B]` `Eras.xml` / `MajorStartingUnits`、`StartingBuildings`）

所有主要文明（含玩家）远古开局基础：

| 行 | 单位 | 数量 | 条件 |
| --- | --- | ---: | --- |
| 基础 | 开拓者 | 1 | 开局在出生格（`OnDistrictCreated=false`） |
| 基础 | 勇士 | 1 | `NotStartTile=true`（不在出生格） |

**仅 AI** 的追加行（`AiOnly=true`；本局神级高于所有 `MinDifficulty`）：

| 单位 | 追加数量字段 | MinDifficulty | DifficultyDelta | 生成时机 |
| --- | ---: | --- | ---: | --- |
| 开拓者 | 1 | `DIFFICULTY_EMPEROR` | 0.5 | 市中心区域创建时（`OnDistrictCreated=true`） |
| 勇士 | 1 | `DIFFICULTY_KING` | 1.0 | 不在出生格（`NotStartTile=true`） |
| 建造者 | 1 | `DIFFICULTY_KING` | 0.5 | 市中心区域创建时 |

- **缺口（重要）**：本机静态资料**没有**逐级换算表（旧 `HandicapInfos` 表在本版本不存在），`DifficultyDelta` 到最终数量的换算无法从静态层求值；上表只给原始行。神级实际追加数量必须靠实机（例如开局后对手城市/单位回读）核对，`(推断)` 一概不用。
- **城邦**：`StartingBuildings` 中远古城墙行 `BUILDING_WALLS / ERA_ANCIENT / MinorOnly=1 / MinDifficulty=DIFFICULTY_IMMORTAL` → **不朽及以上难度，城邦城市开局自带远古城墙**；主要 AI 文明**没有**这条免费城墙行（不要把“神级 AI 开局有墙”当规则）。`[B]`

### 2.2 难度线性缩放（`[B]` `Leaders.xml` 修饰器表；`[A]` 未收录）

挂在通用领袖特性 `TRAIT_LEADER_MAJOR_CIV`，生效条件 `OwnerRequirementSetId=PLAYER_IS_HIGH_DIFFICULTY_AI`（要求 `REQUIREMENT_PLAYER_HANDICAP_AT_OR_ABOVE`，阈值 `DIFFICULTY_PRINCE`）。参数类型全部为 `LinearScaleFromDefaultHandicap`（以默认难度为基准、逐级线性缩放）：

| 修饰器 | 效果 | Amount | Extra（每级差值） |
| --- | --- | ---: | ---: |
| `HIGH_DIFFICULTY_SCIENCE_SCALING` | 城市科技产出 % | 0 | 8 |
| `HIGH_DIFFICULTY_CULTURE_SCALING` | 城市文化产出 % | 0 | 8 |
| `HIGH_DIFFICULTY_FAITH_SCALING` | 城市信仰产出 % | 0 | 8 |
| `HIGH_DIFFICULTY_PRODUCTION_SCALING` | 城市生产力 % | 0 | 20 |
| `HIGH_DIFFICULTY_GOLD_SCALING` | 城市金币 % | 0 | 20 |
| `HIGH_DIFFICULTY_COMBAT_SCALING` | 单位战斗力 | -1 | 1 |
| `HIGH_DIFFICULTY_UNIT_XP_SCALING` | 单位经验 % | 0 | 10 |
| `HIGH_DIFFICULTY_FREE_TECH_BOOSTS` | 每个新时代随机科技尤里卡 | 0 | 1 |
| `HIGH_DIFFICULTY_FREE_CIVIC_BOOSTS` | 每个新时代随机市政鼓舞 | 0 | 1 |
| `BARBARIAN_CAMP_GOLD_SCALING` | 人类清蛮营金币（仅 `PLAYER_IS_HUMAN`） | 0 | -5（参照 `DIFFICULTY_PRINCE`） |

- 阅读方式：**上表是“每级差值”参数，不是神级总值**；`LinearScaleFromDefaultHandicap` 的逐级换算表在本机静态数据中缺失（缺口）。社区常引用的“神级 +32% 科技/文化/信仰、+80% 生产/金币”等总值属于 `(推断)`，不能用本文件当证据；执行时以 `ranking show`/`state overview` 的对手实际产出为准。
- 高难度 AI 还每进入新时代获得**随机科技/市政加速**（数量随难度线性缩放），这是 AI 科技线领先的结构性来源。`[B]`

### 2.3 快速速度（`GAMESPEED_QUICK`）`[B]`

| 参数 | 快速 | 标准 | 含义 |
| --- | ---: | ---: | --- |
| `CostMultiplier` | **67** | 100 | 科技/市政/生产等成本乘数（67%） |
| `CivicUnlockMaxCost` / `PerTurnDrop` / `MinCost` | 75 / 10 / 15 | 100 / 10 / 20 | 市政解锁成本相关（字段语义未在静态资料解释） |
| 全图总回合（`GameSpeed_Turns` 求和） | **330** | 500 | 快速局更短，远古/古典窗口更窄 |
| `QUICK_HALF` / `QUICK_SLIGHT` | 87 / 90 | — | 部分成本类别使用（类别映射未在静态资料标注，缺口） |
| 时长换算示例（`GameSpeed_Durations`，标准→快速） | 5→5、10→9、15→12、29→24、30→25、60→50 | — | 30 回合类协议（承诺/协定）≈25 回合；其它回合字段是否同规则换算未验证 |

- 常见“快速成本”换算 `(推断)`：`成本 = 标准 × 0.67`，取整规则未验证。
  - 科技：制陶/畜牧/采矿 25→≈17；箭术/占星/灌溉/写作 50→≈34；铸铜/砌砖/轮子 80→≈54；骑马/炼铁 120→≈80；工程 200→≈134；马镫 390→≈261。
  - 市政：法典 20→≈13；技艺/对外贸易 40→≈27；军事传统/神秘主义 50→≈34；国家劳动力/帝国初期 70→≈47；戏剧诗歌/政治哲学 110→≈74；军事训练/神学 120→≈80；防御战术 175→≈117。
  - 单位/建筑：勇士 40→≈27；战车 55→≈37；投石兵 35→≈23；弓箭手 60→≈40；开拓者 80→≈54；建造者 50→≈34；圣地（区域）54→≈36；神龛 70→≈47；远古城墙 80→≈54；破城槌 65→≈44；投石车 120→≈80；寺庙 120→≈80。
- 其他速度相关 `[B]`：`TECH_COST_PERCENT_CHANGE_BEFORE_GAME_ERA=-20` / `AFTER=+20`（在全局时代推进前研究更便宜、之后更贵）；`CIVIC_COST_PERCENT_CHANGE_*` 同为 ∓20。

## 3. 宗教胜利机制 与 阻断手段

### 3.1 胜利条件 `[A]`

- `VICTORIES` 行（`VICTORY_RELIGIOUS`）：`EnabledByDefault=1`、`RequiresMultipleTeams=1`、`CriticalPercentage=80`；需求集 `REQUIREMENTS_RELIGIOUS_VICTORY`。
- 需求结构（`[A]` `MECHANIC_VICTORY_RELIGIOUS`）：对“团队内每个成员”检查 `RELIGIOUS_VICTORY_MAJORITY_RELIGION_IS_OWNER`，集合为 `COLLECTION_MAJOR_OPPOSING_PLAYERS` → **你的宗教必须成为“所有其他主要文明”的主流宗教**；不要求每城皈依，只需多数（百科原文）。
- **两级判定（关键）**：① 城市层面——>50% 信徒且信徒最多 → 该城“主流宗教”（predominant）；② 文明层面——按该文明**多数城市**的主流宗教判定该文明的“多数宗教”（百科原文“并不需要文明城市里的所有人都信奉您的宗教，仅需多数人即可”；`[A]` 规则卡只给出需求结构，逐城→文明的完整换算由引擎计算，静态层未求值）。宗教胜利=对每个“其他主要文明”都满足 ②。
- 团队规则：团队创建的宗教成为其他每个文明的主流宗教即可，允许多个宗教组合（`[A]` 百科段）。
- 万神殿不是正式宗教：万神殿**不向外传播**、不能被替换，仅万神殿不能算已创建可争胜的宗教（`[A]` 百科/规则卡条件）。
- **极小地图宗教名额 = 3**：`[B]` `Map_GreatPersonClasses`（TINY `MaxWorldInstances=3`，Duel 2 / Small 4 / Standard 5 / Large 6 / Huge 7）；大预言家类 `MaxPlayerInstances=1`。本基准 T1 实机回读同样显示“3/3 slots remaining — Great Prophet needed”`[C]`。→ **本局 4 个文明中最多 3 家能创教，必有一家无宗教**。
- 创立宗教方式：大预言家激活（`LOC_UNIT_GREAT_PROPHET_DESCRIPTION`）；大预言家点数来源 `[B]`：
  - 圣地区域 `+1/回合`；神龛 `+1/回合`；寺庙 `+1/回合`；政策「启示 / Revelation」（神秘主义市政）`+2/回合`；
  - **圣地祷告者项目**（`PROJECT_ENHANCE_DISTRICT_HOLY_SITE`，成本 25）完成时 **+10 大预言家点数**，并每回合按生产力 15% 转信仰；
  - 伟人基准成本：`Eras.GreatPersonBaseCost` 远古 30、古典 60（具体成长/速度修正静态资料未求值，缺口）。
- 万神殿：`RELIGION_PANTHEON_MIN_FAITH=25`（首个万神殿 25 信仰）；创教初始信条数 `RELIGION_INITIAL_BELIEFS=2`（随后可强化再加）。

### 3.2 城市主流判定、压力与圣城 `[A][B]`

- 城市“主流宗教”：**>50% 公民为该教信徒、且该教信徒数最多**；主流宗教城市向 **10 格**内城市施加压力（百科原文；`RELIGION_SPREAD_ADJACENT_CITY_DISTANCE=10`）。
- 压力参数 `[B]`：相邻每回合 `1`；圣城压力倍率 `×4`、圣城每人口 `200`；圣地倍率 `×2`；商路：起点 `0.5`、终点 `1.0`；无神论/无信仰压力每人口 `50`。
- 圣城：宗教创建城市；转圣城信仰会触发**宗教紧急状况**（其他文明可联合要求该城放弃新宗教，见百科紧急事件段）。占领圣城后宗教归属/压力后果，本机静态资料未解释（缺口），需实机核对。
- 神学战斗力加成（百科）：晋升、政策、夹击/支援、神权政体、处于/相邻本教圣地、位于本教城市领土、**位于本教圣城领土获得极大加成**；参数 `COMBAT_RELIGIOUS_FOLLOWING_CITY_TILE=5`、`COMBAT_RELIGIOUS_HOLY_CITY_TILE=15`。

### 3.3 宗教单位与传播/清除 `[A][B]`

| 单位 | 信仰购买价 | 移动 | 宗教战斗力 | 传播次数 | 异教清退 `ReligionEvictPercent` | 前置建筑 | 关键动作 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 传教士 Missionary | 75 | 4 | 100 | 3 | 10 | 神龛 | 传播宗教；**不能发起**神学战斗（可防御） |
| 使徒 Apostle | 200 | 4 | 110 | 3 | 25 | 寺庙 | 传播、神学战斗、**发起宗教审讯（每教 1 次）**、传道信仰（每教 1 次） |
| 审判官 Inquisitor | 75 | 4 | 75 | 3 | **75** | 寺庙 + 需已发起宗教审讯 | **清除异教**（清退异教信徒）、神学战斗 |
| 上师 Guru | 120 | 4 | 90 | 0 | 0 | 寺庙 | 治疗自身与相邻友方宗教单位 3 次；不能发起神学战斗 |

- 宗教单位**采用购买城市的主流宗教**（不能把玩家创建的宗教当所有单位默认，`[A]` 规则卡条件）。
- 使徒「发起宗教审讯」：每场游戏/每宗教 1 次，**解锁审判官购买**（需寺庙城市）。
- 传播/清除规则：移动至城市中心相邻（己方城市可进入城内）使用「传播宗教」；审判官「清除异教」按 `ReligionEvictPercent=75` 清退异教信徒。
- 神学战斗：仅使徒/审判官能**发起**，无需宣战；战斗结果伤害按宗教战斗力。**被神学战斗击杀**：10 格内城市该教压力下降 = 满血使徒强度，同时胜方宗教获得同等压力；**被军事单位击杀**：6 格内城市该教压力下降且**减半**（`RELIGION_SPREAD_COMBAT_VICTORY=250`、`RELIGION_SPREAD_RANGE_COMBAT_VICTORY=6`、`RELIGION_SPREAD_UNIT_CAPTURE=125`）。`[A][B]`
- 神学战争会有负面外交后果（宗教压力下降引发不满），但并不等于宣战。

### 3.4 外交/战争层面的宗教约束 `[A][B]`

- 「承诺不会转变城市信仰」`DIPLOACTION_KEEP_PROMISE_DONT_CONVERT`：`Duration=30`（快速 ≈25 回合），拒绝/违约加重不满（`DiplomaticActions_XP2`：`GrievancesForRefusal=25`、`GrievancesPerIncursion=25`、`FavorCost=30`）；前提是对方已实施过该行动（百科：Discuss 需先发生过）。
- 「圣战」宣战理由 `DIPLOACTION_DECLARE_HOLY_WAR`：`RequiresConvertedCity=1`（对方转变过你某城信仰），`InitiatorPrereqCivic=CIVIC_DIPLOMATIC_SERVICE`（文艺复兴市政），`WarmongerPercent=50`（好战减半）；奇观/文化压力等不在此列。
- 宗教紧急状况：转变他教圣城信仰可触发，成员国需使该圣城放弃新宗教并维持若干回合；失败对方获大量压力（百科段）。`[A]`

### 3.5 阻断宗教胜利的可执行顺序（规则效力，不保证成功）

1. **自己创教**（本图名额仅 3）：圣地区域（占星术）→ 神龛 → 圣地祷告者（+10 GPP/次）+“启示”+2 GPP → 大预言家创立宗教；随后 **使徒发起宗教审讯 → 审判官「清除异教」**保住己方城市，并用使徒神学战斗翻转压力。没有宗教 → 没有审判官与神学战斗，防守手段只剩军事。
2. **神学战斗击杀**对方宗教单位：10 格压力反转效率最高（a. 3.3 数值）。
3. **军事击杀**对方宗教单位：6 格压力下降且减半，效率低但不需要宗教（宣战即可攻击）。
4. **把任一第三文明的主流宗教翻成“非对手宗教”**（例如用己方传教士把巴西/加拿大的多数城市转为己方宗教）：宗教胜利要求“所有其他主要文明”都是对手宗教，只要有一家不是即失败（3.1）。这比消灭对手更省力，但需要己方宗教与传播单位。
5. **军事占领/消灭对手**：攻占据点、消灭达宗教胜利的文明是从根上移除该胜利来源；代价是长期战争、战争疲劳与好战/怨恨（§4）。
6. **外交拖延**：承诺不传教（30 回合）、圣战理由、紧急状况；只能延缓，不能单独阻止。

### 3.6 本基准实机对照（`[C]`，非规则保证）

- 同一基准两轮均在终局败给**瑞典（克里斯蒂娜）的宗教胜利**：`games/2026-10-25-four-leaf-to-end/runs/20261025-1137/reports/final-report.md`（T100 判负）与 `games/2026-10-25-four-leaf-t1/runs/20261025-1241/README.md`（T129 判负）。
- T129 终局宗教读回（`…/evidence/relay2/cli/debug-response-004273.json`）：`RELIGION (founded 3/3)`；苏美尔 `majority=RELIGION_PROTESTANTISM, founded religion=False`；瑞典 `founded religion=True（新教）native religion cities 23`；巴西 `founded religion=True（印度教）native religion cities 1`。
- 由此得到的规则层执行结论：**本局若不在“3 个先知名额”内创教，宗教防守只剩军事/征服**；而对抗瑞典的宗教胜利，最便宜的结构性阻断是“让任一对手文明的主流宗教 ≠ 新教”（自创教后翻转巴西/加拿大，或消灭瑞典）。**时间窗极紧**：同基准实机在 T100/T129 即被宗教终结，且 T118 时瑞典已能与玩家开战（军力 444 vs 我方 123）。

## 4. 早期战争机制（远古/古典攻城、城墙、好战与议和）

### 4.1 城市防御与城墙时间线 `[A][B]`

- 市中心：`HitPoints=200`（`Districts`）；城市战斗力取决于文明内**最强近战单位**战斗力（最低 10）或驻军；每座未被掠夺区域提供额外加成（百科）；`COMBAT_POPULATION_PER_STRENGTH=2`（每 2 人口 +1 战力）、`COMBAT_RIVER_DEFENSE=5`（河流防御）。
- 远古城墙 `BUILDING_WALLS`：成本 **80**、前置 **砌砖 TECH_MASONRY（80）**、`OuterDefenseHitPoints=100`、`OuterDefenseStrength=3`；建成后城市可发动**城市远程攻击**；只有先击毁外围防御才能攻击城市本体（百科/描述）。
- 中世纪城墙 `BUILDING_CASTLE`：成本 220、前置 `TECH_CASTLES`（390）+ 远古城墙；文艺复兴城墙 `BUILDING_STAR_FORT`：成本 300、前置 `TECH_SIEGE_TACTICS`（730）+ 城墙。`[B]`
- 修复：外墙未被攻击 ≥3 回合才可用「修复外围防线」项目；外墙每回合自然回血 `COMBAT_HEAL_CITY_OUTER_DEFENSES=10`、冷却 3 回合。`[A][B]`
- 对快攻的含义：**无墙城市（仅 200 HP）与有墙城市是两种目标**；神级主要 AI 不会免费获得远古城墙（§2.1，城墙行 `MinorOnly=1` 仅城邦），但 AI 研究砌砖后会自行造墙。实机显示 T51 我方城市已建墙、T118-T129 双方城墙互有损伤 `[C]`。

### 4.2 攻击城墙的伤害规则 `[A][B]`

- 攻击有墙城市时对**外围防御**的伤害：**近战 15%**、**远程 50%**、**轰炸 100%**（`COMBAT_DEFENSE_DAMAGE_PERCENT_MELEE=15` / `RANGED=50` / `BOMBARD=100`；百科同文）。
- 攻城支援（`[A]` `MECHANIC_COMBAT_SIEGE`，`[B]` 能力参数）：
  - **破城槌 Battering Ram**：成本 65、前置砌砖；相邻城市时，使适用的**近战/抗骑兵**单位对城墙造成全额伤害；对**中世纪及以后城墙/城防无效**；过时于「土木工程」市政。
  - **攻城塔 Siege Tower**：成本 100、前置**机械 TECH_MACHINERY（中世纪）**；相邻城市时使适用的近战/抗骑兵单位**无视城墙**；对**文艺复兴及以后城墙/城防无效**。
  - 两者生效的晋升类仅 `PROMOTION_CLASS_MELEE` 与 `PROMOTION_CLASS_ANTI_CAVALRY` → **战车（`PROMOTION_CLASS_HEAVY_CAVALRY`）吃不到攻城支援**。`[B]`
  - 围城：城市/军营四周 6 格被敌军占据/受控制区影响/不可通行时被围，停止回血（百科）。
- 轰炸单位：**投石车 Catapult** 成本 120、前置**工程 TECH_ENGINEERING（200）**、`Bombard=35`、射程 2；不能一回合内移动并攻击（除非“专家组”晋升）。这是远古/古典破墙的正规解。`[B][A]`

### 4.3 战争发起、不满、疲劳、议和 `[A][B]`

| 项目 | 数值/规则 | 来源 |
| --- | --- | --- |
| 突袭战争 | `WarmongerPercent=150`，占领 150、焚城 450 | `[A]` `DIPLOACTION_DECLARE_SURPRISE_WAR` |
| 正式战争 | `WarmongerPercent=100`，占领 100、焚城 300；需对方谴责过你/你至少 5 回合前谴责过（`DenouncementTurnsRequired=5`） | `[A]` `DIPLOACTION_DECLARE_FORMAL_WAR` |
| 圣战 | `WarmongerPercent=50`；对方需转变过你城市信仰；需 `CIVIC_DIPLOMATIC_SERVICE` | `[A]` `DIPLOACTION_DECLARE_HOLY_WAR` |
| 首个战争理由 | 研究「帝国初期」市政后获得首个宣战理由（百科） | `[A]` 百科 DIPLO_3 |
| 好战衰减 | `DIPLOMACY_WARMONGER_POINT_PERCENT_DECAY=50`；`WARMONGER_REDUCTION_IF_AT_WAR=40`、`IF_DENOUNCED=20`、`LIBERATE_POINTS=32`、`STOP_OCCUPYING_REFUND_PERCENT=-100` | `[B]` GlobalParameters |
| 怨恨（Grievances） | 占领首都 3/回合、非首都 1/回合；谴责 25；破坏承诺 ×200%；城邦相关 50/100；`FAVOR_GRIEVANCES_*` 系列 | `[B]` |
| 战争疲劳（War Weariness） | 外国领土战斗 +2、盟友领土 +1、单位被杀 +3、核武 +10；每回合衰减：战时 50、平时 200、宣战和平后 2000；`WAR_WEARINESS_POINTS_FOR_AMENITY_LOSS=400`（400 点 → 宜居度下降）；战时/被占城市额外宜居损失 3/1 | `[B]` |
| 最短战争期 | `DIPLOMACY_WAR_MIN_TURNS=10`；最短和平期 `DIPLOMACY_PEACE_MIN_TURNS=10`；AI 议和提议间隔 `AI_TURNS_BETWEEN_PEACE_OFFERS=3` | `[B]` |
| 议和动作 | 「和解」`DIPLOACTION_MAKE_PEACE`（`Agreement=1`、`Duration=10`）；「提出和平协议」`PROPOSE_PEACE_DEAL` | `[A]` |
| 谴责与宣战延迟 | `DIPLOMACY_DENOUNCE_WAR_DELAY=5` | `[B]` |

- 单次占领的即时好战：`WARMONGER_CITY_PERCENT_OF_DOW=50`（以宣战好战为基准的百分比）；`WARMONGER_FINAL_MAJOR_CITY_MULTIPLIER=200`（灭国最后一座主要城市惩罚 ×2）。
- 回合类参数在快速速度下是否按 `GameSpeed_Durations` 换算未在静态资料中标注（缺口）；`DIPLOMACY_*_MIN_TURNS` 等以实机 `turn status`/外交回执为准。
- 议和能否被接受还取决于 AI 态度、军力对比、已被占领城市与战争疲劳，静态资料只能给前置与参数，不能预测接受条件（`[A]` 规则卡限制）。

## 5. 科技 / 市政优先顺序候选与解锁

> 只列规则层候选与解锁关系；顺序不是固定脚本，需按每回合地图/资源/对手事实取舍（AGENTS 约束）。

### 5.1 远古科技（标准成本；快速≈×0.67；尤里卡=40%进度）`[A][B]`

| 科技 | 成本 | 前置 | 尤里卡（40%） | 关键解锁 |
| --- | ---: | --- | --- | --- |
| 制陶术 Pottery | 25 | — | 无 | 谷仓（65）；→ 写作、灌溉 |
| 畜牧业 AH | 25 | — | 无 | 牧场/营地改良；→ 箭术 |
| 采矿业 Mining | 25 | — | 无 | 矿场/采石场；→ 砌砖、铸铜、轮子 |
| 砌砖 Masonry | 80 | 采矿业 | 建造 1 座采石场 | **远古城墙 80、破城槌 65** |
| 铸铜术 Bronze Working | 80 | 采矿业 | 击杀 3 个蛮族单位 | **军营区域**、矛兵（25）、→ 炼铁 |
| 轮子 The Wheel | 80 | 采矿业 | 开采 1 种矿产 | 重型战车（**与苏美尔战车无关**） |
| 箭术 Archery | 50 | 畜牧业 | 用投石兵击杀 1 个单位 | 弓箭手（60） |
| 写作 Writing | 50 | 制陶术 | 遇见另一个文明 | **学院区域** |
| 占星术 Astrology | 50 | — | 发现 1 个自然奇观 | **圣地区域**、神龛（70） |
| 灌溉 Irrigation | 50 | 制陶术 | 改良 1 种农产资源 | 种植园 |
| 骑马 Horseback Riding | 120（古典） | 箭术 | 建造 1 座牧场 | 骑手（80，需马） |
| 炼铁 Iron Working | 120（古典） | 铸铜术 | 建造 1 座铁矿 | 剑士（90，需铁） |
| 工程 Engineering | 200（古典） | 轮子 | 建造远古城墙 | **投石车 120**、水渠、浴场 |

### 5.2 远古/古典市政（标准成本；鼓舞=40%）`[A][B]`

| 市政 | 成本 | 前置 | 鼓舞 | 关键解锁 |
| --- | ---: | --- | --- | --- |
| 法典 Code of Laws | 20 | — | 无 | 政策：纪律（对蛮族 +5 战力）、君主崇拜（首都 +1 信仰/+1 金币）、城市规划（所有城市 +1 生产力）、勘测 |
| 技艺 Craftsmanship | 40 | 法典 | 改良 3 个单元格 | 政策：斯巴达教育（远古/古典近战/抗骑兵/远程 +50% 生产）、农奴制（建造者 +30%） |
| 对外贸易 Foreign Trade | 40 | 法典 | 发现新大陆 | 商人；政策：商队旅馆、海洋工业 |
| 军事传统 Military Tradition | 50 | 技艺 | 清除蛮族哨站 | **政策：机动（远古/古典重/轻骑兵 +50% 生产）**、将军；→ 军事训练 |
| 神秘主义 Mysticism | 50 | 对外贸易 | 建立万神殿 | 政策：启示（+2 大预言家点/回合）、灵感（+2 大科学家点/回合）；→ 神学 |
| 国家劳动力 State Workforce | 70 | 技艺 | 建造任意特色区域 | **政府区域**；政策：征兵（单位维护 -1 金币）、徭役（远古/古典奇观 +15%） |
| 帝国初期 Early Empire | 70 | 对外贸易 | 文明人口 ≥6 | **首个战争理由**；政策：殖民（开拓者 +50%）、土地测量员、边防军 |
| 政治哲学 Political Philosophy | 110 | 国家劳动力+帝国初期 | 会晤 3 个城邦 | **一级政体：独裁/寡头/古典共和**；外交政策 |
| 戏剧和诗歌 Drama & Poetry | 110 | 帝国初期 | 建造 1 个奇观 | 剧院广场 |
| 军事训练 Military Training | 120 | 军事传统 | 建造 1 个军营 | 政策：马术 Orders（骑兵经验/移动）、掠夺（掠夺收益翻倍）、老兵（军营生产 +30%） |
| 神学 Theology | 120 | 戏剧诗歌+神秘主义 | **创立 1 个宗教** | 寺庙（120）；→ 君权神授 |
| 防御战术 Defensive Tactics | 175 | 游戏娱乐+政治哲学 | 成为被宣战目标 | 政策：棱堡（城防）、防线（城墙/建筑通胀） |

### 5.3 路线候选（条件化，不锁定）

- **A. 战车军事窗口**：战车**零科技**即可出 → 早期优先生产力（`城市规划` +1、`斯巴达教育` 不适用于战车）与 **机动 +50% 战车产能**（军事传统 50，鼓舞=清蛮营，与史诗任务同动作）。科技按“砌砖（城墙/破城槌）/ 铸铜（军营、矛兵）/ 箭术（弓箭手）/ 工程（投石车）”取舍；若走攻城需另带近战或抗骑兵 + 破城槌（战车吃不到支援）。中古前转骑士（马镫 + 铁）。
- **B. 宗教防守/竞争**：占星术 → 圣地 → 神龛 → 圣地祷告者项目（+10 GPP/次）→ 神秘主义（启示 +2 GPP）→ 大预言家创教（3 个名额先到先得）→ 神学 → 寺庙 → 使徒发起宗教审讯 → 审判官防守；文化侧同时需要法典→技艺→对外贸易→神秘主义（神秘主义需对外贸易）→戏剧诗歌/神学。
- **C. 经济/科技**：制陶术 → 写作（学院）→ 砌砖/铸铜；靠金字形神塔（+2 科技，河边 +1 文化）与免前置改良补科研。学院/圣地落位与相邻加成需 `city district`/`get_district_advisor` 回读。
- 通用节拍：全局时代推进前研究的关键科技便宜 20%、之后贵 20%（§2.3）；AI 每个新时代拿随机尤里卡，科研/文化竞争要按 `ranking show` 的动态事实调整，不按固定顺序硬跑。

## 6. 四叶草 FourLeafClover 地图

- **静态层结论：本机知识库与安装数据中没有 FourLeafClover 地图脚本。** 证据：
  - `[A]` `civ6_knowledge` 十主题中无 `Maps`/地图脚本表；`sources/snapshot.json` 未收录地图脚本内容；
  - `[B]` 本机 `DebugConfiguration.sqlite` 的 `Maps` 表（30 行）无 `FourLeafClover`/Four/Clover；`Maps.xml` 与 `Assets/Base/Assets/Maps/*.lua`（Pangaea、Continents、Fractal、Seven_Seas 等 20 余个）也不含该脚本；
  - 该名字只出现在本局存档元数据与项目基准说明中：`civ6-mcp/fixtures/saves/benchmark_gold_tun_1.json` 的 `native_menu_metadata.map_script = "FourLeafClover"`（`map_size=MAPSIZE_TINY`、`difficulty=DIFFICULTY_DEITY`、`speed=GAMESPEED_QUICK`、`ruleset=RULESET_EXPANSION_2`）。
- 因此“几块叶片陆地、玩家间距、海陆比例”**属于本局动态事实**，静态层无法给出，需从实机地图/存档核对；不要在报告中复述名称推断。
- 可确认的地图尺寸参数 `[B]`（`Maps.xml` / `MapSizes`）：
  - `MAPSIZE_TINY`：`GridWidth=60 × GridHeight=38`（2280 格）、`DefaultPlayers=4`、`NumNaturalWonders=3`、`PlateValue=3`、`Continents=2`；
  - 配置库 `MapSizes`（`StandardMapSizes`）另给：`DefaultPlayers=4`、`DefaultCityStates=6`、`MinCityStates=0`、`MaxCityStates=10`、`MinPlayers=2`、`MaxPlayers=6`（本局实际对手为瑞典/巴西/加拿大 3 家，配置为 4 家文明）；
  - 大预言家全图上限 **3**（§3.1）。
- 本基准 T1 实机回读（`[C]` `games/2026-10-25-four-leaf-t1/runs/20261025-1241/evidence/relay/cli/debug-response-000016.json`）：`Turn 1 | 苏美尔 | 神`、金币 6、科技/文化/信仰 0、`Research: None | Civic: 法典`、1 开拓者 + 1 勇士、`Explored: 4% of land (20/449 tiles)`（**本局陆地共 449 格**）、`Religion: none yet (3/3 slots remaining)`、时代阈值 黑暗 9 / 黄金 22。
- 陆地连通性旁证（`[C]`，非规则）：同基准记录显示 T57 敌方重型战车可抵达乌尔城下、T118 瑞典骑兵/骑士进攻玩家城市（`games/2026-10-25-four-leaf-t1/runs/20261025-1241/README.md`）→ 至少“叶片”之间存在可通行陆路/或 AI 已完成上船；不能据此确认叶片数或玩家间距。跨海/跨叶进攻要核对「造船术（TECH_SHIPBUILDING，200）后所有单位可上船」这一规则（`[A]` 移动百科：航海术→建造者、天文导航→商人、造船术→所有单位）。

## 7. 关键结论（务必先读）

### 7.1 关于「阻止宗教胜利」

- **判定是“所有其他主要文明”的主流宗教 = 对手宗教**；任一其他文明主流宗教不是它 → 对手不能宗教胜利（§3.1）。因此阻断不必消灭对手，“让一个第三文明不被同化”即可。
- **极小地图只有 3 个宗教名额、本局 4 文明**（`Map_GreatPersonClasses` TINY=3；T1 实机 3/3 slots）→ 必有 1 家无宗教；**不创教就没有审判官、神学战斗与传播手段**，只能军事击杀（6 格压力下降减半）或征服/消灭。
- **最高效阻断是神学战斗击杀**（10 格内压力反转：被击杀方失去满血使徒强度、击杀方获得同量），其次是审判官「清除异教」（每条 75% 清退），再次是军事击杀（6 格、减半）。
- **创教竞速的正规工具**：圣地（占星术 50）+ 神龛（70）+ **圣地祷告者项目（25，+10 大预言家点/次）** + 启示政策（+2/回合）；万神殿 25 信仰起步。创教后可立刻用使徒发起宗教审讯（解锁审判官）。
- **外交工具只能延缓**：承诺不传教（30 回合≈快速 25）、圣战宣战理由（需对方先转变你城市信仰 + 外交事务市政，好战减半）、转圣城触发宗教紧急状况。
- **本基准历史结果**：瑞典（克里斯蒂娜）在 T100/T129 两次宗教胜利；T129 终局瑞典新教 native cities 23、我方 `founded religion=False`（`[C]`）。规则层推论：在 T80 前若既未创教、又未用军事手段实质性削弱瑞典/翻转任一对手宗教，宗教失败风险极高。
- **缺口**：占领圣城后宗教归属与压力的确切后果、压力到信徒的完整换算、难度/速度对协议时长的逐项换算，静态层不完整，需实机核对。

### 7.2 关于「远古战车快攻」

- 战车是**零科技、零资源、0 维护、30 战力、3（平地 4）移动、忽略 ZOC、成本 55（快速≈37）** 的单位（§1.3）；前期战力明显高于勇士 20/投石兵 5，能压过开局 AI 单位与城邦。
- **抗骑兵 +10 对战车不生效**（标签 `CLASS_HEAVY_CHARIOT`/`CLASS_WAR_CART`，不在抗骑兵匹配集合内）→ 战车不怕矛兵；但**战车吃不到破城槌/攻城塔支援**（支援只给近战/抗骑兵晋升类），对城墙只能打出 15% 的对墙伤害。
- **攻城窗口分两类**：无墙城市只有 200 HP，战车可速推；AI 研究砌砖（80，快速≈54）建墙后，需近战/抗骑兵 + 破城槌（砌砖 65）或投石车（工程 200，Bombard 35）才高效破墙。**神级主要 AI 不会免费获得远古城墙**；但**神级城邦城市自带远古城墙**（不朽+，`MinorOnly=1`）。
- **加成来源**：机动政策（军事传统 50，鼓舞=清蛮营）+50% 骑兵生产 = 战车产能核心；「纪律」对蛮族 +5 战力利于清营；苏美尔史诗任务让每次清营额外给村庄奖励，形成“清营→鼓舞→产能/科技/金币”闭环。
- **过期**：马镫（390，快速≈261）后战车过期，升级目标骑士（50 战力、需铁、220 成本）；因此战车窗口就压在“远古—古典早期”，快速速度下更短。
- **风险与代价**：宣战（远古只有突袭战争，好战 150%）、占领城市好战/怨恨、战争疲劳、AI 神级战斗力/产出缩放；攻城若拖到 AI 起墙+出骑士，战车性价比骤降。攻/守都要用本回合 `unit list`/`map area`/`city list` 回读，不按历史记录假设。

## 8. 静态层缺口清单（不要在这些点上编造结论）

1. 神级 AI 逐级加成总值：本机无 `HandicapInfos` 表，`DifficultyDelta`/`LinearScaleFromDefaultHandicap` 的换算不可求值（§2）。
2. `GameSpeed_Scalings` 的 `SCALING_HALF/SLIGHT` 具体适用类别、所有回合类参数（协议、最短战争期等）的快速换算。
3. FourLeafClover 地图脚本与结构（叶片数、玩家间距、海陆比）——本机知识库/安装数据均无，需实机地图核对。
4. 占领圣城后宗教归属/压力后果；压力→信徒的完整换算；宗教胜利最终判定由引擎计算（`[A]` 明示未求值）。
5. 议和/AI 接受条件、战争疲劳与怨恨的完整外交效果（静态资料只给参数与前置）。
6. 各表数值不证明本局有效成本/资格/地形合法性；执行前用 `get_city_production`、`get_tech_civics`、`get_units`、`get_map_area`、`get_religion_spread`、`get_victory_progress` 等回读。

## 9. 参考文件

- 知识入口：`civ6_knowledge/README.md`、`civ6_knowledge/manifest.json`（规则快照与边界）。
- 规则卡：`civ6_knowledge/rules/civilizations.json`（苏美尔/吉尔伽美什/战车/神塔）、`combat.json`（`MECHANIC_COMBAT_SIEGE`/`MECHANIC_COMBAT_ATTACK`）、`victory.json`（`MECHANIC_VICTORY_RELIGIOUS`/`MECHANIC_VICTORY_BELIEFS_SPREAD`）、`diplomacy.json`（`DIPLOACTION_DECLARE_HOLY_WAR`/`DIPLOACTION_KEEP_PROMISE_DONT_CONVERT`/`DIPLOACTION_MAKE_PEACE`）、`technologies.json`、`civics.json`。
- 安装数据（只读）：`Civ6.app/Contents/Assets/Base/Assets/Gameplay/Data/{Difficulties,GameSpeeds,Maps,Eras,Leaders,Units,Buildings,Improvements,GreatPeople_Prophets,GlobalParameters,DiplomaticActions,Victories}.xml`；`.../Cache/DebugGameplay.sqlite`、`DebugConfiguration.sqlite`。
- 基准配置：`civ6-mcp/fixtures/saves/benchmark_gold_tun_1.json`、`civ6-mcp/fixtures/saves/README.md`。
- 历史实机（对照，非规则）：`games/2026-10-25-four-leaf-t1/runs/20261025-1241/README.md`、`games/2026-10-25-four-leaf-to-end/runs/20261025-1137/reports/final-report.md`、`games/2026-10-25-four-leaf-cli-100/runs/20261025-1424/README.md`。
