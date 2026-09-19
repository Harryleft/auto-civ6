# 实验 Runtime Surface 能力清单

本清单是 `civ_mcp.runtime.server` 的唯一能力声明。**未在“已支持”中逐项
列出的任何能力均为 unsupported**；模型不得从旧 `civ-mcp` 的工具名、旧文档或
`CivMutationFactory` 中推断实验入口也支持该能力。

它描述的是当前隔离实验入口，不代表正式 `civ-mcp`。正式切换仍须满足 K1 的真实
单机 smoke 与 recovery 验收。

## 已支持

| 类别 | 工具 | 运行时保证 |
| --- | --- | --- |
| 局面读取 | `get_runtime_context`、`get_unit_promotions`、`get_unit_attack_target`、`get_city_attack_target`、`get_district_placements`、`get_builder_improvement_candidates`、`get_pending_deals`、`get_trade_negotiation`、`get_city_states`、`get_governors`、`get_governments`、`get_policies`、`get_city_purchases`、`get_city_production`、`get_trade_destinations`、`get_trade_routes`、`get_world_congress`、`get_climate_overview`、`get_spies`、`get_religion_overview` | 前者一次读取 overview、城市、待决城市占领、单位、外交/会话、科技/市政、万神殿、时代着力点、大人物和胜利进度；同时返回 `pending_decisions`（仅当前进程、可恢复的 turn blocker 的事实快照）、`unfinished_intents` 与 handoff。两种攻击目标读取只确认一个由坐标指定、游戏当前允许的实时目标；`get_district_placements` 只返回实际 `BUILD` 操作接受的格点，不自行过滤水格；`get_builder_improvement_candidates` 仅检查建设者当前格，绝不探测远程格点；`get_pending_deals` 返回双方结构化实际条款，`get_trade_negotiation` 只读 DealManager 当前的 `PROPOSED`、`COUNTER_OFFER` 或 `UNKNOWN`；`get_world_congress` 只读当前 session、决议、目标与提案，绝不投票或提交；`get_climate_overview` 只读 Gathering Storm 的气候阶段、CO2、灾害风险与近期事件，在非 Gathering Storm 规则集会明确失败而不会返回空状态；`get_spies` 只读特工位置、状态与实时合法任务，绝不发起旅行、任务或逃脱路线；`get_religion_overview` 只读已创宗教、信徒聚合、已见面大文明的宗教/万神殿状态与己方信仰收支，缺少主记录会明确失败，绝不伪装成空事实；其余细粒度读取按工具名返回当前领域事实，单项失败在 `unknown` 中显式保留。 |
| 战略连续性 | `save_handoff` | 仅写当前 branch 的战略重点、已有安排、理由和改变条件；不修改游戏事实或 operation。 |
| 常规 mutation | `move_unit`、`attack_unit`、`attack_city`、`build_improvement`、`upgrade_unit`、`promote_unit`、`send_envoy`、`appoint_governor`、`assign_governor`、`promote_governor`、`change_government`、`set_policies`、`set_city_production`、`purchase_item`、`make_trade_route`、`propose_trade`、`set_research`、`set_civic`、`found_city`、`choose_pantheon`、`choose_dedication`、`recruit_great_person` | 每次请求绑定 game/branch/operation/decision turn，经 `SessionKernel` 单次发送，且仅由领域 readback 确认。两种攻击都只提交实时合法目标，且只在指定目标减血或消失时确认；目标读回缺失或血量未变均为 `UNKNOWN`。`build_improvement` 仅允许建设者在当前格执行，且只以该固定格的新改良设施读回确认。`set_city_production` 的区域类型必须带一个 `get_district_placements` 返回的格点，写入前的 `CanStartOperation` 失败时不发送，并要求城市回读同时出现该区域类型与指定格点。`propose_trade` 严格校验插入 Lua 的参数；DealManager 的直接回读为 `PROPOSED` 或结构化 `COUNTER_OFFER` 才确认，无法获得直接状态则为 `UNKNOWN`，不得自动接受。对待决报价的模型选择在同一报价消失后以 `ACCEPTED` 或 `REJECTED` 记录；条款发生变化则重新成为 `COUNTER_OFFER`。其余 mutation 保持各自候选与领域读回契约。 |
| 回合 | `end_turn`、`resume_turn_decision` | 原 end-turn 只发送一次；等待期只读轮询。当前可恢复的 blocker 是单一城市占领选择、单一且条款完整的待决交易回价、世界议会、单一普通外交会话，以及有可用使者时的城邦选择。交易回价的 `choice` 仅为 `ACCEPT` 或 `REJECT`，会把实际双方条款返回模型；世界议会当前只有模型显式选择的 `SUBMIT_ABSTAIN`，不投票；使者的 `choice` 为返回的城邦 player ID。 |

`UNKNOWN`、`RECOVERY_REQUIRED` 与 `NEEDS_DECISION` 都不是成功，也不会触发重试。
`pending_decisions` 不持有 continuation；进程重启后它会为空，原 operation 仍保留在
`unfinished_intents` 中并进入显式 Recovery 边界，绝不伪造可恢复的决策。Context 在组合
多项读取前后都会核验同一 Session game/branch binding；切档或换局时整份上下文被拒绝，绝不
混入旧 branch 的 operation。

## 明确不支持

- 任意 Lua、FireTuner 直连、启动/终止游戏、重启、存档、读档、端口占用处置和
  Recovery：这些均不是模型工具。Recovery 仍只可由 host 在稳定 checkpoint 与
  新 branch identity 下显式调度。
- 旧 `civ-mcp` 的 Belief/Governance、PlayProfile、pipeline、Web/Convex、世界模型、
  后台 watcher、自动弹窗处理与任何自动策略。
- `get_runtime_context` 未包含的细粒度读取，例如地图/视野、战斗或路径估算、奇观格点候选、
  外交交易选项、单位/宗教详情。
- 尚未暴露的写操作：建设者的修复、清除和筑路，非唯一/条款不完整交易的接受或拒绝、宗教创立/信条选择/传播/增强（万神殿除外）、间谍、世界议会投票、奇观及
  任何未列在上表的 mutation。
- F2 尚未覆盖多会话仲裁。Runtime 不会自动接受不同条款、自动投票、自动保留城市或自动重发 end-turn。

新增工具前，必须同时补充本清单、相应 precheck/领域 Evidence、架构边界测试与
完整回归；否则不属于支持能力。
