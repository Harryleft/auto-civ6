# 神级四叶草：T1 → T129（净推进 128 回合，终局失败）

2026-10-25，神级、快速、极小四叶草；苏美尔／吉尔伽美什，seed `-1896272000`。从受信原始基准 `fixtures/saves/benchmark_gold_tun_1.Civ6Save`（SHA-256 `0d25d557…fe3abf`，两段推进后复核未变）重新开始，由单一前台 `civ6 debug` 会话（经前台转接器）连续推进到原生终局。

推进分两段，同一测试轮次、同一游戏进程：

1. **T1 → T101**：净推进 100 回合（100 次确认 `turn end`），独立成段留档。
2. **T101 → T129**：续推 28 回合，直至终局。

| 项目 | 结果 |
| --- | --- |
| 终局 | **第 129 回合，DEFEAT —— 克里斯蒂娜（瑞典）达成宗教胜利**（`turn end` 返回 `GAME OVER — DEFEAT. 克里斯蒂娜 of 瑞典 won a Religious victory.`；`state overview` 末行 `GAME OVER: DEFEAT — 瑞典, VICTORY_RELIGIOUS`） |
| 我方终局 | 4 城、25 人口，总分 **173**，科技 22／市政 14，科技 36.1／回合、文化 7.7／回合、金币 824，军力 188 |
| 对手终局 | 瑞典 624（9 城、38 科技、军力 497）、巴西 514（5 城、40 科技）；新教已在全部文明占多数 |
| 城市终局 | 乌鲁克（10，首都）、基什（6）、乌尔（6）、巴地比拉（4）；乌鲁克/基什城墙受损（89/100、74/100），农场被掠夺 |
| 单位终局 | 4 战车；三城已排产骑士但来不及完成 |
| 检查点 | 25 份（T5…T125）；终局画面拒绝保存，最后 CLI 检查点为 `fourleaf_t1_t125` |
| 未决 | 终局时无 pending、无 transport_unknown，`quit` 正常 |

## 过程节点

| 回合 | 事件 |
| --- | --- |
| T1–T5 | 加载受信基准，建立新一轮检查点归属；建都乌鲁克（41,26） |
| T13 | 法典完成，首次填充军事/经济政策槽 |
| T27 / T45 / T78 | 依次建立基什（38,24）、乌尔（37,28）、巴地比拉（34,25） |
| T62 / T87 / T112 | 三次世界议会；T87 进入文艺复兴（正常时代）并选“雄伟壮丽”；T95 转古典共和 |
| T101 | 前段 100 回合完成，保存 `fourleaf_t1_t101` |
| T118–T120 | 瑞典宣战并出现骑兵/骑士/卡洛琳军；我方军力 123 对 444，转入守城 |
| T121–T128 | 战车持续消耗与补产，城堡、马镫、数学完成；三城改产骑士；城墙受损、农场被掠 |
| T129 | 瑞典宗教胜利，游戏结束 |

## 终局判定与证据

- `turn end`：`GAME OVER — DEFEAT. 克里斯蒂娜 of 瑞典 won a Religious victory.`
- `state overview` 末行：`GAME OVER: DEFEAT — 瑞典, VICTORY_RELIGIOUS`
- `victory show`：`RELIGION (founded 3/3)`，瑞典新教 `native religion cities 23`，巴西 1；其余胜利路线均未完成，首都均未失守（失败来自宗教，不是被攻陷）
- 终局 `save create` 返回 `Error: GAME_OVER|Saving is unavailable at the confirmed terminal boundary`（原生约束，保留最后检查点与终局证据）；原生 `AutoSave_0129` 存在但不属于 CLI 登记检查点

## 两段实际使用的 CLI 操作

- 生命周期：`game stop` → `game launch` → `save menu-load benchmark_gold_tun_1`；续段直接连回运行中的游戏。
- 每回合：`turn status`、`state overview`、`unit list`、`city list`、`diplomacy list`、`technology status`、`notifications show`；所有变更先 `--dry-run`。
- 扩张与发展：`map settle`、`unit found-city`、`city set-production`（含区域 `--x --y`）、`city district`、`city production`。
- 单位：`unit move/attack/fortify/heal/skip/cancel-move/improve/tasks/sleep/automate/upgrade`。
- 研究、政策、政体：`technology set`、`civic set`、`policy show/set`（按槽位类型匹配，过时政策自动换候选）、`government list/change`。
- 交互与强制选择：`popup dismiss`、`era dedications/choose`、`governor list/assign/promote`、`congress show/votes`、`diplomacy peace`（冷却期被拒，未成交）。
- 存档：`save create` 每 5 回合；终局保存被原生拒绝。

## 阻塞项处理记录（操作经验）

| 阻塞/暂停 | 处理 |
| --- | --- |
| 生产/科研/市政阻挡 | 读取候选显式选择；空政策槽按类型填充，`POLICY_OBSOLETE` 时换下一个候选 |
| 科技完成、历史时刻、自然奇观、`NaturalDisasterPopup`、`EraCompletePopup` | `popup dismiss`（可叠加多次）后沿原 `turn end` 确认 |
| 世界议会 `WORLD_CONGRESS_SESSION`（含 `STALE`） | 按阶段重新登记票单 `congress votes '[...]'`，再 `turn end` 确认；`UNITPROMOTIONCLASS` 选重骑兵、宗教决议选新教（对瑞典不利） |
| 时代着力点 | 推进请求未决时 `era choose` 被门禁拒绝；先确认原 `turn end`，再选择 |
| 政体/总督 | `CONSIDER_GOVERNMENT_CHANGE` → 转古典共和并填槽；`GOVERNOR_IDLE` 先 `assign`，`OPPORTUNITY` 再 `promote` |
| 移动队列 `QUEUED_MOVE_CURRENT` / `READINESS_UNKNOWN` | `unit cancel-move` 后显式 `fortify`，再推进 |
| 战时 | 城墙 + 要塞政策（`POLICY_BASTIONS`）、自然哲学（学院相邻加成）、战车补产与 `unit upgrade`；对骑士/骑兵不主动近战送死 |
| `TILE_ACTION_REFUSED` / `NO_ENEMY` 等原生拒绝 | 视为该次动作未提交，换候选或跳过，不重复同一意图 |

## 脚本侧问题（本地驾驶脚本，不属于 CLI/Runtime）

- 建城选址正则把“临水”写成可选组，首都落在 41,26（首选 40,25）。已修正。
- `"UNIT" in blocker` 误匹配 `ENDTURN_BLOCKING_GOVERNOR_OPPORTUNITY`（OPPORT**UNIT**Y），导致总督机会被当成单位阻塞反复空转。已改为精确匹配。
- 政策填充只尝试首个候选，遇到 `POLICY_OBSOLETE` 不换候选；世界议会暂停分支只走外交路径；`NO_ENEMY` 未纳入软失败。均已修正。
- 策略偏保守：未造侦察兵（终局探索 19%）、文化极低（7.7/回合，未建纪念碑）、终局仍有闲置开拓者；与神级 AI（624/514）差距明显。这是策略取舍，不是引擎或 CLI 问题。

## 检查点（Single 目录，本轮登记；21 份早段 + 5 份续段）

| 存档 | 字节 | SHA-256（前 16） |
| --- | ---: | --- |
| `fourleaf_t1_t5` | 555422 | `38d50cd1d406a2a9` |
| `fourleaf_t1_t10` | 584623 | `f523de061f192b04` |
| `fourleaf_t1_t20` | 615789 | `1fc08ecee7db192b` |
| `fourleaf_t1_t25` | 627292 | `49c6f5717580ea99` |
| `fourleaf_t1_t30` | 636160 | `39ec40e1c766ecdb` |
| `fourleaf_t1_t35` | 659180 | `676625549e368305` |
| `fourleaf_t1_t40` | 672750 | `94906df27e51e054` |
| `fourleaf_t1_t45` | 694412 | `0dc8d650c60f5e5b` |
| `fourleaf_t1_t50` | 702947 | `5c25a7b7b2ba0b27` |
| `fourleaf_t1_t55` | 725933 | `c69560b314f68b5a` |
| `fourleaf_t1_t60` | 741350 | `e94329baa33de554` |
| `fourleaf_t1_t65` | 743705 | `caa3f573871777a4` |
| `fourleaf_t1_t70` | 767006 | `db708875db901869` |
| `fourleaf_t1_t75` | 771596 | `986e89efd65b45fb` |
| `fourleaf_t1_t80` | 788825 | `6e8cfa384750c7be` |
| `fourleaf_t1_t85` | 785884 | `ac7186bdbe9c8969` |
| `fourleaf_t1_t90` | 789997 | `6fdac9d7da2085bb` |
| `fourleaf_t1_t95` | 801538 | `22e5baf8737e2aa7` |
| `fourleaf_t1_t100` | 816635 | `0948bc45e29bfa16` |
| `fourleaf_t1_t101` | 822568 | `528849241956d1f1` |
| `fourleaf_t1_t105` | 821491 | `d82584dc20faef19` |
| `fourleaf_t1_t110` | 839694 | `5b9b9bb38d4c3257` |
| `fourleaf_t1_t115` | 843311 | `520ca21e8782a400` |
| `fourleaf_t1_t120` | 865231 | `18071e679be5a8de` |
| `fourleaf_t1_t125` | 875526 | `73db4576475afafe` |

T129 终局画面不可保存；原生 `AutoSave_0129` 仅作本地参考，不属于 CLI 登记检查点。

## 证据

| 内容 | 位置 |
| --- | --- |
| 逐回合摘要（123 条，T2–T129） | [turns.jsonl](reports/turns.jsonl) |
| 阻挡项处理轨迹 | [blockers.log](reports/blockers.log) |
| 第一段（T1→T101）请求/回执 38MB | `evidence/relay/` |
| 续段（T101→T129）请求/回执 22MB | `evidence/relay2/` |
| 自动驾驶状态 | [autopilot-state.json](reports/autopilot-state.json) |
| 本轮暴露的 CLI 缺口修复与实机验证 | [changes/cli-gap-fixes.md](changes/cli-gap-fixes.md) |

## 验证范围

- 实机通过：原始基准加载（身份校验）、T1→T129 共 128 次确认推进、生产/科研/市政/政策/政体/总督/世界议会/时代着力点/弹窗/单位战斗与升级/存档/终局检测。
- 原始基准指纹未变；其他对局与检查点未改动。
- 本轮未修改仓库代码；驾驶脚本在 `.scratch/`，不入库。
- 未覆盖：外交对话与会话、交易、和平谈判成交（仅经历战争与被拒的和平提议）、热座、Lua、玩家胜利路线。
