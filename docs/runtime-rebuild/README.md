# Runtime Core Replacement

## 冻结基线

新 Runtime 的冻结基线是 Git 提交
`1d92a723b18609d734b6aacdda26ce815f390a21`，本地标记为
`runtime-core-pre-rebuild`。旧核心仍可通过该提交独立检出和复现。

这项重构不会在旧的 Belief / Governance / 多层 pipeline 运行链上继续增加
功能。旧链在新 Runtime 完成切换前仅接收阻止数据损坏、安全问题或无法启动的
紧急修复；新能力必须落在 `civ_mcp.runtime` 和 `civ_mcp.civ` 的目标依赖方向上。

## 重构范围与运行边界

目标是建立一条单一、可验证的执行路径：Civ6 保存世界事实，Runtime 保存操作
事实和连续性，模型负责游戏判断。切换前旧链保持只读的历史参考价值，不能成为
新 Runtime 的依赖或 fallback。

每张任务卡独立提交、独立验证。完整计划见项目外的
`Civ6_Runtime_Core_Replacement_DSM_Execution_Plan.md`；本目录只记录实际
落地的边界与迁移状态，而不以计划文本替代验收证据。

实验入口当前可支持及明确不支持的能力见
[`capabilities.md`](capabilities.md)。未在该清单中逐项列出的能力一律不支持。

## 已落地的独立核心

- Operation contract、SQLite operation store、完整 frame transport，以及独立的
  FireTuner 握手/Lua-state 发现都位于 `civ_mcp.runtime`；它们不依赖旧
  `GameConnection`、`GameState` 或 `civ6_belief_engine`。
- `CivAdapter` 已提供 game identity、overview、cities、units、diplomacy 和
  victory progress 的 typed read，并只提交一次 mutation。科技/市政读取另外提供
  当前选择的稳定 `TECH_*` / `CIVIC_*` ID，而不是依赖本地化名称；万神殿读取同样
  提供稳定 `BELIEF_*` ID 与当前可选候选；时代着力点读取则提供当前允许的索引和
  激活的 `COMMEMORATION_*` 类型；大人物读取提供 individual ID 与本地认领状态。
- `SessionKernel` 是 mutation 的唯一入口：发送后取消、连接中断、跨局回读
  都只会留下 `UNKNOWN`，同一 `operation_id` 的并发调用只会实际发送一次。
- 新的 `TurnLoop` 只输出 `ADVANCED`、`NEEDS_DECISION` 或
  `RECOVERY_REQUIRED`。它在 end-turn 发送后只读轮询，并且仅以新的 turn
  Evidence 关闭原 operation；不补发 end-turn，也不启动恢复。明确 `NOT_SENT`
  的请求直接要求新决策，不进入等待或恢复。
- Runtime 可以只读识别一个活跃外交会话并返回带会话事实的 `DIPLOMACY`
  interrupt。模型经 `resume_turn_decision` 选择 `POSITIVE`、`NEGATIVE` 或仅
  goodbye 阶段的 `EXIT`；运行时只发送一次明确响应，随后继续原 end-turn，
  不会补发 end-turn。会话未关闭或未推进时仍保留 `UNKNOWN`。
- Runtime 也会把待决城市占领读为 `CITY_CAPTURE` interrupt，提供 Civ6 此刻允许的
  `KEEP`、`RAZE`、`REJECT` 或解放选项。恢复时只发送模型选定的单一指令，再以待决
  状态消失与目标坐标的城市归属/不存在作为领域 Evidence；它不会沿用旧核心的自动
  保留城市逻辑，且不会补发原 end-turn。
- Runtime 在结束回合受可用使者阻塞时返回 `ENVOY` interrupt，候选是可接收使者的
  城邦 player ID；模型选择一个 ID 后仅发送一次 `send_envoy`，再继续等待原 end-turn，
  绝不自动选城邦或补发 end-turn。
- 已迁移到 `CivMutationFactory` 的领域包括移动、单位/城市攻击、升级、晋升、
  生产、购买、商路、建设单元、研究/市政、治理/总督、宗教、大人物、间谍、
  世界议会和 end-turn。每个 factory 都绑定 intent，并要求领域 Evidence。
- 实验入口已把单位升级接入独立执行路径：提交前核对当前单位可升级与目标
  `UNIT_*` 类型，提交后仅以同一单位读回该目标类型确认。
- 单位晋升读取同时返回当前可选与已拥有的 `PROMOTION_*` 类型；实验入口以它们分别
  作为发送前资格与发送后证据。
- 城邦读取返回可用使者及已会面城邦的精确使者数和派遣资格；实验入口仅在目标城邦
  使者数恰好增加一、可用使者数恰好减少一时确认 `send_envoy`，不能以 Lua 的成功文本
  代替领域证据。
- 总督读取区分已拥有和当前合法的晋升；实验入口仅在任命/晋升后点数恰好减少一，或
  派驻后同一总督的目标城市 ID 精确匹配时确认操作。
- 政策读取为每张候选卡提供当前合法的槽位；实验入口拒绝跨槽或重复配置，并仅在每个
  请求槽位读回指定政策后确认。
- `civ_mcp.runtime.server` 是独立的实验 FastMCP 入口，当前仅注册
  `get_runtime_context`、`get_unit_promotions`、`get_city_states`、`get_governors`、`get_governments`、`get_policies`、`save_handoff`、`move_unit`、`upgrade_unit`、
  `promote_unit`、`send_envoy`、`appoint_governor`、`assign_governor`、`promote_governor`、`set_city_production`，
  `end_turn` 与 `resume_turn_decision`，以及 `set_research`、`set_civic`、
  `choose_pantheon`、`choose_dedication`、`found_city`。`save_handoff` 仅保存当前
  branch 的战略重点、已有安排、理由和改变条件，不能修改游戏事实或 operation record。
  另有 `recruit_great_person` 可招募当前候选、`send_envoy` 可派遣一个已验证合法的使者、
  `change_government` 可切换至一个已验证解锁的政府、`set_policies` 可配置已验证合法的政策槽位。
  它要求 host 显式提供 `CIV_MCP_RUNTIME_BRANCH`，不会自行猜测读档分支。
  server 只负责工具装配；end-turn 的等待和证据关闭属于 `TurnLoop`，而非 MCP
  路由函数。

可用下列方式启动实验入口（它仍不是正式 `civ-mcp` 命令）：

```bash
CIV_MCP_RUNTIME_BRANCH=<稳定存档分支标识> \
  .venv/bin/python -m civ_mcp.runtime.server
```

该入口不会启动游戏、加载存档、自动重连后重发 mutation，或调用旧的
Belief/Governance 链路。

## 不变量

- 同一对局在任意时刻只有一个写入 owner。
- 没有新的领域 Evidence，`UNKNOWN` 不会自动升级为任何结论。
- 每个 mutation 均绑定 `game_id`、`branch_id`、`operation_id`；读档产生新 branch。
- 模型工具只能经 SessionKernel 提交 mutation，不能直接连接 FireTuner。
- Recovery、Telemetry、Context 和 UI 不能修改操作执行事实。
- Recovery 在稳定 identity 验证后也必须创建新的 `branch_id`；不能把恢复后的
  世界重新绑定为旧时间线。

## 当前限制

工作树已有未跟踪的 `design/graph-idea-visual.html`，它不是本次重构产物，不能
作为“工作树干净”的验收证据，也不会被纳入本重构的提交。

正式切换尚未发生，`civ-mcp` 仍指向旧 server。以下条件尚无完成证据，因此
不得执行 K1--K4 删除/切换：

- F2 已接入单一活跃外交会话和单一待决城市占领；交易回价、世界议会和多会话仲裁
  仍未迁移；
- 新实验 surface 只支持能力清单中的最小集合；虽已逐项声明 unsupported，仍未覆盖
  旧入口的大部分读写能力；
- 尚未在真实单机游戏中完成新 surface 的 read → mutation → end-turn smoke，
  或真实 recovery 验证；
- `pyproject.toml` 仍会打包 `civ6_belief_engine`，旧 server/pipeline 与
  play-profile 双轨也仍存在。

这些不是可由离线测试替代的条件。达到它们前，旧核心只作为正式入口，新的
Runtime 只作为隔离的实验实现。
