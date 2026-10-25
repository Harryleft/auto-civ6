# 神级四叶草 T50→终局：最终效果与运行观察

## 结论（最终效果）

| 项目 | 结果 |
| --- | --- |
| 终局 | **第 100 回合，失败** — 瑞典（克里斯蒂娜）达成 **宗教胜利** |
| 终局画面 | `state overview` 末行 `GAME OVER: DEFEAT — 瑞典, VICTORY_RELIGIOUS`；`turn end` 返回 `GAME OVER — DEFEAT. 克里斯蒂娜 of 瑞典 won a Religious victory.` |
| 我方 | 苏美尔／吉尔伽美什，4 城、23 人口，总分 **198**，科技 40.2／回合，文化 25.8／回合，军力 223，金币 702（+20/回合） |
| 对手 | 巴西 455、加拿大 434、瑞典 423；宗教胜利时新教已是 4/4 文明多数派 |
| 起点对比 | T50：2 城、12 人口、总分 97、科技 14.3；T100：4 城、23 人口、198、40.2 |
| 原始基准 | `benchmark_gold_tun_1`，SHA-256 仍为 `0d25d557…fe3abf`，未改动 |
| 检查点 | T51/60/75/80/85/95/100 共 7 份已登记存档（可由 `game recover` 使用） |
| 最终未决 | 无 pending、无 transport_unknown，CLI 正常 `quit`，游戏未重启 |

一句话：**从 T50 的落后局面撑到 T100，先后经历被宣战、守城、议和、扩张到 4 城、忠诚叛乱丢掉基什、跌入黑暗时代，最后被瑞典的宗教胜利终结，官方判定失败。**

## 过程关键节点

| 回合 | 事件 |
| --- | --- |
| T51 | 选定戏剧和诗歌、殖民政策；乌尔修墙 |
| T57 | 瑞典与纳斯卡城邦**同时宣战**；敌方重型战车抵达乌尔城下，两弓箭手齐射击退 |
| T58–T61 | 守城战：失去勇士与 1 名弓箭手，但城墙完成后乌鲁克/乌尔均未被攻破；研究出学徒、工程、机械 |
| T64 | 世界议会第一次投票（大科学家 +100%、对瑞典不满 +100%） |
| T67 | 与瑞典**议和成功**；随后和平称霸期开始 |
| T70 | 招募大科学家（宾根的希尔德加德）、研究炼铁术 |
| T70–T88 | 扩张到 4 城：阿达卜（38,28）、基什（31,21）、第 4 城（43,28）；商业中心、学院、政府区陆续建成 |
| T78–T89 | 第二次世界议会；和平与巴西、瑞典均转为 **FRIENDLY** |
| T89 | 基什因忠诚度归零叛乱，成为自由城市后**请求加入巴西**（我方净失 1 城） |
| T90 | 进入文艺复兴**黑暗时代**；选择“雄伟壮丽”着力点 |
| T91–T99 | 科技推进到印刷术、教育、马镫；城市人口与产出稳步增长 |
| T100 | 瑞典宗教胜利，游戏结束 |

## 本轮实际验证的 CLI 能力

- 单一持久 `civ6 debug` 会话（经前台转接器 socket），全程无并行 MCP 客户端；所有变更均先 `--dry-run` 再执行。
- 命名命令覆盖：单位（移动/攻击/驻防/跳过/取消移动/改良/自动化/商路/晋升）、城市（生产/区域选址/远程攻击）、科技与市政、政策、总督（任命/晋升）、使者、伟人招募、世界议会投票、外交对话与交易回应、时代着力点、自然奇观/科技完成等弹窗关闭、存档创建、终局检测。
- 回合推进共 **50 次确认**（T50→T100），`turn end` 的推进回执逐次核对；出现暂停时按外交/议会/强制选择分流后沿原入口确认。
- 3.5 千余条转接器请求留痕于 `evidence/relay*/`；逐回合摘要见 `reports/turns.jsonl`。

## 着色点：本轮暴露的问题与 CLI 覆盖缺口

1. **`game recover` 只认“自己登记过的检查点”**。第一次故障尝试恢复 `AutoSave_0060`（原生自动存档，正是 T60 开局）被拒绝：`Checkpoint completion and expected identity are unavailable`。原生自动存档不在检查点登记表中，CLI 无法用它做恢复，**恢复粒度被限制在 CLI 自己 `save create` 过的存档**。
2. **失败的 `game recover` 会把会话送入不可恢复状态**。`restart_and_load` 重启游戏后 180 秒内未确认加载，返回 `OUTCOME_UNKNOWN`；随后任何 RPC 会超时并锁存 `transport_unknown`，CLI 本地只允许只读，连 `quit` 都拒绝，只能结束会话保留证据。
3. **旧 MCP 进程可能独占 FireTuner 连接**。会话被替换后，上一会话的 `civ-mcp` 子进程仍持有 4318 端口连接，新会话报 `Cannot connect to Civ 6 at 127.0.0.1:4318`；本次靠手工 `kill` 该孤儿进程才恢复。CLI 没有列出/清理/接管孤儿连接的命令，`pending` 仅反映本进程内存。
4. **终局画面拒绝保存**：`save create` 返回 `Error: GAME_OVER|Saving is unavailable at the confirmed terminal boundary`（文档已声明，属预期，但意味着终局只能保留最后一份检查点）。
5. **`city_range_attack` 阻挡项与“无城墙城市”矛盾**：无墙城市不能远程攻击（`NO_WALLS`），但回合状态仍可能要求“攻击或显式确认”，只能靠 `popup dismiss` 消解，语义不够直观。
6. **单位叠放会静默变成 CANNOT_OPERATE**：同格两个军事单位时，`unit fortify/skip` 直接被原生拒绝，`turn end` 报 `ENDTURN_BLOCKING_UNITS`；需要先读 `map area` 找可通行相邻格再显式移动，CLI 没有“拆叠”入口。
7. **移动队列阻挡需显式处理**：`turn end` 返回 `NOT_SUBMITTED (QUEUED_MOVE_CURRENT)`，必须 `unit cancel-move` 后再 `fortify`/新移动；不会自动沿用队列。
8. **外交回应的确认语义**：回应未确认时重复同一调用是“确认”，但当前选项可能已变化；实测需要“同一回应确认 + 再发一次当前选项（如 EXIT）”两步才能关闭会话。`NO_CURRENT_VIEW` 的已核验会话需 `diplomacy close PLAYER_ID`。
9. **世界议会**：`STAGE1/STAGE2` 与 `local request: STALE` 需要按阶段重新登记；无资格提案的阶段要用 `congress votes '[]'` 显式推进，否则回合停在 `ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION`。
10. **没有原生“单位自动作战/城市自动生产”命令**：长线推进本质上是“每回合多次查询 + 逐项命名命令”，一次 `turn end` 前后常需 10~20 次 RPC；本次用受控自动驾驶脚本（只调用命名命令、变更先 dry-run）把 50 回合跑完。

## 常用操作（本轮真实调用频次，3530 条请求）

| 次数 | 命令 | 说明 |
| ---: | --- | --- |
| 788 | `unit fortify` | 部队驻防，长线防守的最高频动作 |
| 443 | `turn status` | 每回合确认阶段/阻挡项/未决 |
| 268 | `unit move` | 移动、回城、拆叠、站位 |
| 217 | `popup dismiss` | 自然奇观/科技完成/时代更替等弹窗 |
| 183 | `unit list` | 取本次实体 ID、坐标、可攻击目标 |
| 158 | `city list` | 生产状态、城墙/驻军、成长与住房 |
| 126 | `turn end` | 推进（含暂停后的原请求确认） |
| 120 | `unit skip` | 清掉就绪单位阻挡 |
| 116 | `city attack` | 城墙后的城市远程攻击 |
| 92 | `state overview` | 总分/排名/研究/时代 |
| 87 | `technology status` | 研究状态与候选 |
| 83 / 82 | `diplomacy pending` / `diplomacy list` | 会话与关系判定 |
| 77 | `unit attack` | 对敌单位/蛮族攻击 |
| 71 | `city set-production` | 生产排程（区域需坐标） |
| 63 | `diplomacy respond` / `50` | 对话回应（POSITIVE/EXIT） |
| 63 | `trade pending` | 来约检查（本轮全部拒绝） |
| 52 | `unit tasks` | 建造者改良候选 |
| 33 | `technology set` | 选科技 |
| 30 | `unit improve` | 农场/牧场/采石场/种植园/伐木场 |
| 25 | `unit automate` | 侦察兵自动探索 |
| 22 | `policy set` | 生产政策切换（部分因过时被拒） |

## 证据与复现

- 本轮目录：`games/2026-10-25-four-leaf-to-end/runs/20261025-1137/`
- 逐回合摘要：`reports/turns.jsonl`（43 条，T56–T100 后段；T50–T55 的逐动作记录在 `evidence/relay/requests/`）
- 原始请求/回执：`evidence/relay/`（第一段，含故障）、`evidence/relay2/`（失败恢复尝试）、`evidence/relay3/`（最终 T50→T100，39MB）
- 检查点（Single 目录，已登记可 `game recover`）：

| 存档 | 字节 | SHA-256 |
| --- | ---: | --- |
| `fourleaf_1137_t051` | 708275 | `b1bb15e05b26c0903b170b84e0c86fbd2d50a12de7fdca33fa9ec18b52c8286d` |
| `fourleaf_1137_t60` | 727629 | `dc27350985fcd76fde34f20d0295a4ff39891c67544872e3e491f064df345007` |
| `fourleaf_1137_t75` | 768850 | `00e99e0d4755494c22b02e3c405dd91dd465566c88932cf0f1ab68c57117e6e2` |
| `fourleaf_1137_t80` | 781247 | `ba5bd41f6d268174437a632aa3e067e21056c721e889c13cb9db8f2d00998829` |
| `fourleaf_1137_t85` | 798531 | `9f5d3e1fdbbff659bb32f325abc17581573dc0d3dbbb8f02b0bb42db4dc79637` |
| `fourleaf_1137_t95` | 817418 | `5a7a6d5ee3406a75b2f9b8d4c2e77b8cd00029591ce3ac4679b277d7db67cc29` |
| `fourleaf_1137_t100` | 820365 | `c643ddada7537797d167c4d84ce56eec1d4064b9cccf02856020e955393a8ceb` |

复现要点：从 `fixtures/saves/benchmark_gold_tun_1.Civ6Save` 或 `unitcli_20261025_094255_t050` 检查点加载后，T50→T100 的过程不可由脚本重放（无游戏内重放机制），只能以检查点 + 证据对照。
