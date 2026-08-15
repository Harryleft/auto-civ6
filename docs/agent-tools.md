# Civ 6 工具与动作参考

本文是工具层速查。参数、可用能力和规则集差异以当前 MCP schema 与 `get_game_overview` 的 `RUNTIME POLICY` 为准。

## 离线决策助手

### `scripts/civ6_tool.py`

- `dist X1 Y1 X2 Y2`：六边形距离、建城距离、射程和移动判断。
- `settle X Y`：检查与现有城市的距离并评估建城合法性。
- `combat CS1 HP1 CS2 HP2`：离线估算战斗伤害和胜负。
- `status` / `plan` / `plan-save '[[x,y],...]'`：查看或保存扩张计划。

### `scripts/civ6_assist.py`

- `precheck`：结束回合前检查生产、研究、政策、使者、外交、世界议会、机会项和单位状态。
- `units`：单位全景、可行动、可升级和可建造状态。
- `promotions`：批量检查晋升。
- `expansion`：城市、合法建城点和未改良资源。
- `threats`：按阵营、CS、HP 和距离排序威胁。
- `cities`：城市队列、增长、掠夺和城墙诊断。

建议顺序：`precheck` → `get_game_overview` → `get_barbarian_overview` → 必要时治理/提案/反方 → 修复阻塞 → `threats` + 真实 `get_combat_estimate` → 路由 ActionIntent → 动作与 Outcome → `get_turn_brief` → 单位确认 → `skip_remaining_units` → `end_turn`。

## 蛮族态势与清剿

`get_barbarian_overview` 是独立的蛮族查询工具，返回两类信息：

- `Camps`：所有已经揭示的 `IMPROVEMENT_BARBARIAN_CAMP`，即使当前离开视野仍会保留；包含到最近己方城市和军事单位的距离。
- `Visible barbarian units`：当前视野内的蛮族军事单位；包含坐标、HP、CS/RS 和到己方城市/军队的距离。迷雾中的单位不会伪造为已知。

处理顺序固定为：`get_barbarian_overview` → 选择最近且可安全抵达的军事单位 → `get_combat_estimate`（有驻守单位时）→ 必要时路由 `move/attack` → 复核单位 → 移动到营地格清除营地。只击杀营地生成的一个单位不能算完成蛮族处理；没有可见单位时也要按营地位置安排清剿。

`get_game_overview` 会附带一份压缩蛮族摘要，`end_turn` 在推进后也会再次发出营地告警；这两处是防止 AI 因只调用通用威胁扫描而漏掉营地的兜底，不替代独立查询的完整坐标列表。

## 部落村落（遗迹 / goody hut）

"遗迹"（部落村落，`IMPROVEMENT_GOODY_HUT`）是一次性奖励：任一单位踏入即取用并使其消失。注意它与考古文物遗址（archaeology site，由建造者发掘，非 improvement）是两种机制，`get_village_overview` 只管前者。

- `get_village_overview` 返回所有已揭示村落：坐标、可见状态、领土归属、到最近己方城市/战斗单位（含侦察单位）的距离，并按取用优先级（速取/可达/远端）排序。
- 只报告当前仍存在的村落：已取用村落不出现在结果中，工具不保留取用历史；从结果中消失只说明已被某方取用，取用者与时间不可确认。
- 奖励内容在取用后由游戏结算，不可查询；不要为期待特定奖励而推迟取用。
- 使用纪律：探索期按需调用（不必每回合）；侦察单位顺路取用优先，村落会被其他文明抢走，但不要为远端村落偏离战略路线。

## 常见工具陷阱

- 神级 AI 回合可能耗时 5–10 分钟；`end_turn` 失败后不要循环重试。
- AI 交易和外交提议的有效期很短，`end_turn` 返回后立即处理，否则可能得到 `NO_DEAL` 或 `NO_SESSION`。
- 新生产/购买的单位本回合不能移动，通常返回 `NO_MOVES`。
- 军事单位不能与驻军同城堆叠，出现 `STACKING_CONFLICT` 时移到邻格。
- 攻击后立刻重新调用 `get_units`；目标可能已经被击杀，继续攻击会得到 `NO_ENEMY`。
- 无 UI 环境中 `send_envoy` 可能不消耗令牌，导致 `end_turn` 卡在 `GIVE_INFLUENCE_TOKEN`；必要时用 `restart_and_load` 恢复。
- 升级后 `unit_id` 会改变，必须重新读取单位列表。
- “开拓者 killed”通常表示开拓者已转化为城市，不是单位损失。
- 洪泛区、丘陵、森林、丛林会提高移动成本，远距离行动前先用 `get_pathing_estimate`。

## 战斗速查

| 单位 | CS | RS | 射程 |
|------|----|----|------|
| Warrior | 20 | — | — |
| Slinger | 5 | 15 | 1 |
| Archer | 25 | 25 | 2 |
| Barbarian Warrior | 20 | — | — |

- 远程攻击不会受到反击伤害；近战攻击会受到伤害。
- 森林和山脉阻挡远程视线；被阻挡的目标不会出现在攻击列表。
- 加固单位防御 +4，并且每回合恢复生命值。
- 战斗估算应考虑晋升 CS、侧翼、支援以及森林/丛林防御。

## 单位动作

| 动作 | 效果 | 关键约束 |
|------|------|----------|
| `move` | 移动到地块 | 需要 `target_x`、`target_y` |
| `attack` | 攻击敌人 | 返回伤害估算，自动识别近战/远程 |
| `fortify` | 防御 +4 并恢复 | 仅军事单位 |
| `heal` | 加固到满血 | 满血后自动唤醒 |
| `alert` / `sleep` | 哨戒 / 无限休眠 | 需要敌人触发或手动唤醒 |
| `skip` | 结束本回合 | 始终可用 |
| `automate` | 自动探索 | 仅侦察兵 |
| `delete` | 解散单位 | 移除维护费 |
| `found_city` | 建城 | 仅开拓者 |
| `improve` | 建造改良 | 建造者/军事工程师 |
| `remove_feature` | 砍伐或收获 | 移除森林、丛林或沼泽 |
| `build_route` | 建道路/铁路 | 仅军事工程师 |
| `trade_route` | 开始商路 | 商人，目标为城市 |
| `teleport` | 移动闲置商人 | 目标为城市 |
| `activate` | 激活伟人 | 必须位于匹配的已完成区域 |
| `spread_religion` | 传播宗教 | 传教士/使徒 |

常见改良：`IMPROVEMENT_FARM`、`IMPROVEMENT_MINE`、`IMPROVEMENT_QUARRY`、`IMPROVEMENT_PLANTATION`、`IMPROVEMENT_PASTURE`、`IMPROVEMENT_CAMP`、`IMPROVEMENT_FISHING_BOATS`、`IMPROVEMENT_LUMBER_MILL`。

森林、丛林和沼泽会阻挡多数改良。先 `remove_feature`，再 `improve`；伐木场和营地可以直接建在森林/丛林上。用 `get_units` 中的 `valid_improvements` 判断地块是否可用。被掠夺的区域建筑通过 `set_city_production` 修复。

军事工程师的 `build_route` 不消耗次数，但每格铁路消耗 1 铁和 1 煤，并耗尽本回合移动力；`IMPROVEMENT_FORT` 和 `IMPROVEMENT_AIRSTRIP` 会消耗次数。

## 结束回合阻塞项

`end_turn` 推进前会解决阻塞项。返回阻塞时按类别处理：

- 单位未移动：`move` / `skip` / `fortify`。
- 城市生产为空：设置新的生产项目。
- 科技或市政完成：选择下一个。
- 总督点数可用：`get_governors` → `appoint_governor` / `assign_governor` / `promote_governor`。
- 单位有经验：`get_unit_promotions` → `promote_unit`。
- 政策槽为空：`get_policies` → `set_policies`。
- 万神殿/宗教阈值达到：`get_pantheon_beliefs` → `choose_pantheon`，或 `get_religion_beliefs` → `found_religion`。
- 使者可用：`get_city_states` → `send_envoy`。
- 新时代着力点（献礼）：出现阻塞通知时先 `get_dedications`，比较当前时代的候选加成后立即 `choose_dedication(dedication_index=候选索引)`；这是回合必办界面选择，不走理事会审批，但必须以当回合查询到的候选索引为准。
- 时代进度：`get_era_progress` 一次读世界纪元、各文明纪元与（RF/GS）时代分进度；Standard 规则集下时代块显式报告不可用。
- 占领或不忠城市：`city_action(city_id, "keep"/"raze"/"liberate_founder"/"liberate_previous")`。

移动响应显示目标地块而不是到达位置，这是异步寻路的表现。

## 外交、生产与区域

- 外交：`get_pending_diplomacy` → `respond_to_diplomacy`；主动外交使用 `send_diplomatic_action`、`form_alliance`、`propose_trade`、`propose_peace`。交易先 `mode="test"`，确认后才 `mode="send"`。
- 间谍：`get_spies` → `spy_action`；先 `travel`，抵达后再执行任务。
- 城邦：`get_city_states` → `send_envoy`。查询会同时给出我方和已知文明的使者竞争、1/3/6 档奖励、宗主国奖励、活动任务与军事征募可用性；未见面文明可能参与竞争时会明确标为信息不完整。先据此比较目标，再派使者；军事征募本期仅提供读取证据，不执行征募动作。
- 奇观：用 `get_wonder_advisor(city_id, wonder_name)` 选址，再用 `set_city_production`。
- 研究：`get_tech_civics` 按回合排序，≤ 2 回合项目的 `!! GRAB THIS` 标记不要漏掉。
- 购买：`purchase_item` 购买单位/建筑，`purchase_tile` 购买边界地块，`patronize_great_person` 购买伟人。
- 区域：用 `get_district_advisor(city_id, district_type)` 选址，再用 `set_city_production`。

常见区域加成：学院靠山脉/丛林/地热裂缝，圣地靠山脉/森林/自然奇观，工业区靠矿山/采石场/渡槽，商业中心靠河流/港口，剧院广场靠奇观/娱乐中心，军营不能贴市中心。

## 宗教总览

`get_religion_overview` 是世界宗教聚合查询（一次往返）：已创宗教（创立者、圣城、信条构成）、各宗教主流城市数与信徒总数、各已见面大文明的己创/主流/万神殿对照、己方信仰值与创教名额。未见面创立者遮蔽为 Unmet。

分工：逐城主流宗教与信徒明细用 `get_religion_spread`；万神殿/创教候选信条用 `get_pantheon_beliefs` / `get_religion_beliefs`；宗教胜利逼近看 `get_victory_progress`。名额已满（`已用名额 N/N`）时不可再创教，信仰转向购买自然学家/传教士/伟人。

## 商路与伟人

- `get_trade_routes` 查看活动商路和闲置商人。
- `get_trade_destinations(unit_id)` 查看目的地。
- `unit_action(action='trade_route', target_x, target_y)` 开始商路。
- 国内商路提供食物和生产力，国际商路提供金币；外国贸易提供 1 条容量，市场/灯塔各提供 +1。
- `get_great_people` 查看候选人与成本；`recruit_great_person` 用点数招募，`patronize_great_person` 直接购买，`reject_great_person` 跳过。
- `get_great_people_overview` 伟人全景一次返回：各类别全体主要文明点数榜（未met 文明显示 Unmet）、当前候选池、已认领历史、己方在野伟人及激活次数。
- 伟人招募后移动到匹配区域，用 `unit_action(action='activate')` 激活；不要把 0 个建造者次数误认为伟人可以删除。

## 气候（GS）

`get_climate_overview`（Gathering Storm 规则集专属，其他规则集显式报 `ERR:NO_CLIMATE_IN_RULESET`）：海平面阶段（不可逆，phase 4+ 触发沿海资产搬迁告警）、气候变化点数与下次上升倒计时、世界/己方 CO₂ 与最大排放文明、风暴/洪水/喷发/干旱风险百分比、本回合灾害及受影响城市、近期事件史（`history_turns` 默认 30，clamp 1-200）。建议每 ~10 回合或收到洪水/火山/暴雪通知后调用；核事故刻意保留在历史中（与官方历史页口径不同）。

## 世界议会

世界议会在 `end_turn()` 内同步触发，必须先投票：

1. `get_world_congress()`；`turns_until_next = 0` 表示本回合触发。
2. 读取 A/B 选项、目标和支持度成本。
3. `queue_wc_votes(votes='[{"hash": H, "option": 1, "target": 0, "votes": N}]')`。
4. `end_turn()` 推进回合。

每项决议有 1 张免费票；额外票的累计支持度成本为 6/18/36/60/90/126……。外交胜利点决议应集中支持度于最有影响的一项，并验证不会误助对手。

## 相关文档

- [回合规则](agent-turn-loop.md)
- [策略与胜利路线](agent-strategy.md)
- [游戏恢复](agent-recovery.md)
- [架构](architecture-diagrams.md)
- 返回 [AGENTS.md](../AGENTS.md)
