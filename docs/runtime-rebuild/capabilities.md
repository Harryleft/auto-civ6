# 实验 Runtime Surface 能力清单

本清单是 `civ_mcp.runtime.server` 的唯一能力声明。**未在“已支持”中逐项
列出的任何能力均为 unsupported**；模型不得从旧 `civ-mcp` 的工具名、旧文档或
`CivMutationFactory` 中推断实验入口也支持该能力。

它描述的是当前隔离实验入口，不代表正式 `civ-mcp`。正式切换仍须满足 K1 的真实
单机 smoke 与 recovery 验收。

## 已支持

| 类别 | 工具 | 运行时保证 |
| --- | --- | --- |
| 局面读取 | `get_runtime_context`、`get_unit_promotions`、`get_city_states`、`get_governors`、`get_governments` | 前者一次读取 overview、城市、待决城市占领、单位、外交/会话、科技/市政、万神殿、时代着力点、大人物和胜利进度；`get_unit_promotions` 读取一个单位的可选与已拥有晋升；`get_city_states` 读取全部可用使者及所有已会面的城邦。未会面的城邦不在结果中，城邦竞争信息是否完整由每行的 `competition_complete` 标注；`get_governors` 仅在扩展规则集可用，并区分已拥有与当前合法的总督晋升；`get_governments` 读取全部已解锁政府并标记当前政府；单项失败在 `unknown` 中显式保留。 |
| 战略连续性 | `save_handoff` | 仅写当前 branch 的战略重点、已有安排、理由和改变条件；不修改游戏事实或 operation。 |
| 常规 mutation | `move_unit`、`upgrade_unit`、`promote_unit`、`send_envoy`、`appoint_governor`、`assign_governor`、`promote_governor`、`change_government`、`set_city_production`、`set_research`、`set_civic`、`found_city`、`choose_pantheon`、`choose_dedication`、`recruit_great_person` | 每次请求绑定 game/branch/operation/decision turn，经 `SessionKernel` 单次发送，且仅由领域 readback 确认。`send_envoy` 还要求同一城邦使者数恰好增加一、可用使者数恰好减少一；任命和晋升总督要求总督点数恰好减少一，派驻要求目标城市 ID 与读回一致；`change_government` 只在目标类型成为唯一当前政府时确认。 |
| 回合 | `end_turn`、`resume_turn_decision` | 原 end-turn 只发送一次；等待期只读轮询。当前可恢复的 blocker 是单一外交会话和单一城市占领选择。 |

`UNKNOWN`、`RECOVERY_REQUIRED` 与 `NEEDS_DECISION` 都不是成功，也不会触发重试。

## 明确不支持

- 任意 Lua、FireTuner 直连、启动/终止游戏、重启、存档、读档、端口占用处置和
  Recovery：这些均不是模型工具。Recovery 仍只可由 host 在稳定 checkpoint 与
  新 branch identity 下显式调度。
- 旧 `civ-mcp` 的 Belief/Governance、PlayProfile、pipeline、Web/Convex、世界模型、
  后台 watcher、自动弹窗处理与任何自动策略。
- `get_runtime_context` 未包含的细粒度读取，例如地图/视野、战斗或路径估算、生产与
  购买候选、商路目的地、外交交易选项、单位/间谍/宗教详情、世界议会详情和气候。
- 尚未暴露的写操作：单位/城市攻击、购买、建设者、商路、交易提议或
  接受、政策、宗教（万神殿除外）、间谍、世界议会、奇观及
  任何未列在上表的 mutation。
- F2 尚未覆盖交易回价、世界议会和多会话仲裁。Runtime 不会自动接受不同条款、
  自动投票、自动保留城市或自动重发 end-turn。

新增工具前，必须同时补充本清单、相应 precheck/领域 Evidence、架构边界测试与
完整回归；否则不属于支持能力。
