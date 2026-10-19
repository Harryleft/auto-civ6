# 游戏状态变更审计与使者试点

基线：`b24fd1d`，2026-10-19；审计对象为当前 `src/civ_mcp`、MCP 注册入口及 `scripts` 的实际调用链。排除历史归档、测试原生模拟器和当时未纳入版本控制的分享稿（现归档于[团队分享资料](../../games/benchmark-sumeria-719665280/runs/2026-10-18-t233/team-sharing/README.md)）。本记录包含本次使者改造后的状态；其他路径的结论来自静态调用链核对，不表示重新通过实机验收。

## 结论与最小结构

82 个已声明的 MCP 工具中，39 个是查询，43 个能够改变游戏、原生界面、存档或进程状态。`unit_action`、`city_action`、`spy_action` 等还包含多个动作。`CIV_MCP_DISABLE_LUA` 可在启动时移除 `run_lua`，此时实际暴露 81 个工具。

核心缺口是操作生命周期覆盖不全：已有 `ActionLifecycle`、`TurnProgress`、`DiplomacyProgress`，但不少入口直接发原生命令，再把发送回执当成完成，或在有界回读失败后仅返回一条文本。下一次调用没有原意图可核对。通信层不重发不能阻止上层再次发出同一消费操作。

沿用现有 module 和 interface：GameState 准备领域意图，ActionLifecycle 保存一份未决动作，领域 Lua 提交并读取动作特有证据，MCP 串行调度。只将提交、取消、未知保留的共性放入已有所有者；不引入通用事务引擎、事件总线或动作注册框架。这样的 depth 将错误时序收在现有 module 中；领域证据的 locality 保留在对应 Lua 文件。测试 seam 是 MCP／GameState 与原生引擎 adapter，复用现有 Lua 5.1 执行测试带来 leverage。

一个动作必须回答：原意图是什么、何时可能已提交、什么事实能确认完成、什么事实能证明零提交、结果未知时由谁阻止重复提交。`execute_read`／`execute_write` 只是 GameCore／InGame 上下文选择，不能用来判断有无副作用。

## 状态口径

| 标记 | 含义 |
| --- | --- |
| 已有所有者 | 已有跨调用未决记录和领域确认；仍须保留其动作专属验证，不能据此宣称完全正确 |
| 部分确认 | 有结果回读或原生记录，但缺完整 Python 门禁、取消锁存或跨调用确认 |
| 直接提交 | 没有跨调用结果所有者；原生资格检查不能替代结果确认 |
| 显式生命周期 | 保存、进程、菜单或换档；使用现有生命周期接口，不塞入普通动作所有者 |
| 任意执行 | 无法从任意 Lua 推导安全完成条件；保持显式调试接口，不承诺业务事务语义 |

## 43 个变更工具清单

表内文件均位于 `src/civ_mcp/`；`server.py` 是共同入口。

### 单位与城市

| 工具／分支 | 影响与原生命令 | 当前所有者、证据与缺口 | 实现 |
| --- | --- | --- | --- |
| `unit_action.move` | 位置、移动力、可能捕获；MOVE_TO | 已有 ActionLifecycle；位置／原生完成见证；不将超时当失败 | `game_state.py`、`lua/units.py` |
| `unit_action.attack` | 伤害、攻击次数、位置；远程／空袭／近战 MOVE_TO | 已有 ActionLifecycle；攻击者／目标证据 | 同上 |
| `unit_action.found_city` | 消耗移民、新建城市 | 已有 ActionLifecycle；绑定位置城市回读；丢回执可能无法定位完成，保留未知 | `lua/map.py` |
| `unit_action.skip/fortify/cancel_move` | 原生就绪／活动／移动队列 | 已有 ActionLifecycle；请求记录、原身份和活动读数 | `lua/units.py` |
| `unit_action.pillage` | 破坏、移动力、奖励 | 已有 ActionLifecycle；具体地块损坏；不声称测得奖励 | `lua/pillage.py` |
| `unit_action.sacrifice_charges` | 建造次数、项目进度、可能移除单位 | 已有 ActionLifecycle；完整单位身份、原生命令／次数事件、项目证据 | `lua/contribution.py` |
| `unit_action.improve/repair/remove_improvement/remove_feature/build_route` | 地块、次数、资源／生产收益 | 直接提交；部分 repair 分支在 CanStartOperation=false 时仍尝试发送；须先绑定地块和单位全 ID，再确认次数及地块变化 | `lua/units.py` |
| `unit_action.automate/heal/alert/sleep` | 单位任务／活动／就绪 | 直接提交；有资格判断，缺请求所有者；后续沿 readiness 做小步迁移 | `lua/units.py` |
| `unit_action.delete` | 删除单位 | 直接提交；不能仅凭查不到单位确认，须绑定完整身份及移除证据 | `lua/units.py` |
| `unit_action.trade_route/teleport` | 商路、容量、单位位置 | 直接提交；需绑定商人、始末城、路线或转移结果 | `lua/economy.py` |
| `unit_action.activate` | 伟人次数／移除及不同类别效果 | 直接提交；含 RequestOperation／RequestCommand 两条路径，需按效果设计证据 | `lua/great_people.py` |
| `unit_action.spread_religion` | 传教次数与目标信仰 | 直接提交；需目标和次数双侧证据 | `lua/religion.py` |
| `skip_remaining_units` | 多个单位 skip | 已有逐个 ActionLifecycle；首个未决即停，非原子批次 | `game_state.py` |
| `city_action.attack` | 城市射击、目标伤害 | 已有 ActionLifecycle；城市／目标及原生命令见证 | `lua/cities.py` |
| `city_action.keep/reject/raze/liberate_founder/liberate_previous` | 占城、归属、销毁 | 直接提交；工具明确忽略 city_id、取待决队列城市；重复调用可能处理另一座城，优先改为绑定原待决城市 | `lua/cities.py` |
| `spy_action.travel/mission` | 间谍位置、任务与回合 | 直接提交；需完整间谍身份与目的地／任务读数 | `lua/espionage.py` |
| `spy_action.escape` | 逃跑路线选择门禁 | 已有 ActionLifecycle；当前全 ID、路线、门禁推进；不等于存活或到达 | `lua/espionage.py` |
| `set_city_production` | 队列与区域落位 | 已有 ActionLifecycle；生产项、可选区域坐标回读 | `lua/cities.py` |
| `purchase_item` | 金币／信仰、单位或建筑 | 直接提交；须消费与新实体／建筑双侧核对，不能仅凭同名单位存在 | `lua/cities.py` |
| `purchase_tile` | 金币、地块归属 | 直接提交；需城市／地块绑定及费用与归属证据 | `lua/map.py` |
| `upgrade_unit` | 金币／资源、单位类型与身份 | 直接提交；入口将 ID 取模，须防止旧全 ID 命中新单位 | `lua/governance.py` |
| `promote_unit` | 晋升、stored promotions、回血、通知 | 部分确认；GameCore SetPromotion 后同步 HasPromotion，但后续清次数、回血、通知有多步骤副作用；无持久未决 | `game_state.py`、`lua/governance.py` |
| `set_city_focus` | 城市 Citizens 偏好、忽略状态 | 已有 ActionLifecycle；多个标准 SET_FOCUS 命令，最终 Citizens 目标态核对 | `lua/cities.py` |

### 帝国资源与治理

| 工具 | 影响与原生命令 | 当前所有者、证据与缺口 | 实现 |
| --- | --- | --- | --- |
| `send_envoy` | 一枚可用使者、目标城邦影响力 | **本次试点**：ActionLifecycle＋原生请求记录；同意图重入仅确认；两侧计数与世界绑定 | `actions.py`、`lua/envoys.py` |
| `appoint_governor` | 总督头衔、任命 | 直接提交；需头衔消费和精确总督存在证据 | `lua/governance.py` |
| `assign_governor` | 总督指派城市 | 直接提交；需精确总督／目标城；建立完成不是指派完成 | 同上 |
| `promote_governor` | 头衔、晋升 | 部分确认；一次 HasPromotion 回读，缺超时／丢回应未决所有者 | 同上、`game_state.py` |
| `set_research` 的 tech／civic | 科研／市政选择 | 部分确认；InGame 后一次读取不匹配即走 GameCore setter；异步未生效不能证明零提交。迁移时保留原生资格检查，移除推断式 fallback | `game_state.py`、`lua/tech.py` |
| `set_policies` | 政策槽位、可能付费解锁 | 部分确认；完整槽位计划及有界核对，失败返回 POLICIES_UNCONFIRMED 后无未决记录；解锁与换卡可能部分生效 | `lua/governance.py` |
| `change_government` | 政体与 considered 标志 | 直接提交；SetGovernmentChangeConsidered＋RequestChangeGovernment，需目标政体核对 | 同上 |
| `choose_dedication` | 时代承诺 | 直接提交；需时代／剩余选择／目标承诺绑定 | 同上 |
| `choose_pantheon` | 信仰／万神殿 | 直接提交；需原世界宗教状态和精确信条核对 | `lua/religion.py` |
| `found_religion` | 创建宗教＋两个信条 | 直接提交；三个 PlayerOperation，须记录每步及部分完成，不能整段重发 | 同上 |
| `recruit_great_person` | 伟人池、点数与新单位 | 直接提交；需 exact individual 与招募结果 | `lua/great_people.py` |
| `patronize_great_person` | 金币／信仰、伟人池与新单位 | 直接提交；消费型，优先核对费用与 individual 双侧结果 | 同上 |
| `reject_great_person` | 伟人资格／跳过当前个体 | 直接提交；需绑定原 individual，避免拒绝下一位 | 同上 |

### 外交、议会与回合

| 工具 | 副作用 | 当前所有者、证据与缺口 | 实现 |
| --- | --- | --- | --- |
| `respond_to_trade` | 接受／拒绝当前来约 | 已有 DiplomacyProgress；会话、条款、原生资格和终结记录 | `diplomacy.py`、`lua/trade.py` |
| `negotiate_incoming_trade` | 修改 working deal；INSPECT／EQUALIZE 消息 | 已有 DiplomacyProgress；不是纯查询，保留原会话／条款指纹 | 同上 |
| `propose_trade`（含 test_only） | 创建会话、清理／填写 working deal、评估／提案／接受 | 已有 DiplomacyProgress；test_only 仍改变外交界面与草案，不能归为查询 | 同上 |
| `respond_to_diplomacy` | 当前语句选择 | 已有 DiplomacyProgress；原生选项、语句序号与后续状态 | 同上 |
| `propose_peace` | 和约会话及回应 | 部分确认；有界和平回读但不跨调用保存原请求 | `game_state.py`、`lua/diplomacy.py` |
| `send_diplomatic_action` | 友谊、使团、大使馆、谴责、各类宣战 | 友谊／使团／大使馆仅部分确认；其他直接提交。OPEN_BORDERS 转已有交易所有者。宣战成功后还启动延迟视图清理任务，须纳入原请求的世界／会话绑定 | 同上 |
| `form_alliance` | 同盟会话、条款、回应 | 部分确认；双方同盟类型回读，无跨调用未决 | 同上 |
| `open_world_congress` | 打开原生投票界面 | 原生开始记录绑定世界／回合／阶段，重复仅看可见状态；Python 无独立 pending 所有者，需复核其他入口不能绕过未决开始 | `lua/congress.py` |
| `queue_wc_votes` | 逐项投票、提交、请求回合继续、关闭 UI | 原生票计划和阶段记录；有部分提交阶段，不能与普通 end_turn 当一个原子操作；后续补齐全局门禁 | 同上 |
| `end_turn` | 推进世界 | 已有 TurnProgress；原世界、本地玩家、起始回合、就绪／终局证据；外交和议会是显式阻塞解除路径 | `end_turn.py` |
| `dismiss_popup` | 通知 dismissal、UI close、可能解除原生事件队列 | 外交部分有 DiplomacyViewCleanup；其他原生弹窗按明确入口关闭，不能视为只读；须保留未决消费记录 | `game_lifecycle.py` |

### 存档、进程与任意执行

| 工具 | 副作用 | 当前所有者、证据与缺口 | 实现 |
| --- | --- | --- | --- |
| `save_game` | 原生保存、文件、检查点索引 | 显式生命周期；SaveComplete＋新文件＋世界／回合复核。Python pending 在返回 unknown 后才设，取消窗口需补；原生 SaveGame 抛错／false 的零提交解释需复核 | `game_state.py`、`game_lifecycle.py`、`checkpoints.py` |
| `load_save` | 按已展示列表索引选档、替换世界 | 显式生命周期；解析到文件名后走统一 `_replace_save`，绑定已确认检查点 | 同上 |
| `load_game_save` | 主菜单加载指定档 | 显式生命周期；发出前 replacement_pending，确认世界／回合／活动和快照后释放 | 同上 |
| `reload_game_save` | 退菜单并换档 | 同一替换所有者；退出菜单与加载分别保存阶段事实 | 同上 |
| `load_save_from_menu` | 菜单加载工具别名 | 调用 `load_game_save`；没有第二套加载器 | `server.py` |
| `kill_game` | 停止游戏、断开连接、退役旧会话 | 显式生命周期；进程停止事实、replacement_pending | `game_launcher.py`、`game_state.py` |
| `launch_game` | 启动进程 | 显式生命周期；仅启动，不等于已加载世界 | 同上 |
| `restart_and_load` | 停止、启动、加载、确认 | 显式恢复；保留既有检查点保护；MCP 明确放行此恢复入口 | 同上 |
| `run_lua` | 任意上下文中的任意副作用 | 任意执行；无通用后置条件或跨调用业务确认。已有未决时受门禁，可用环境变量移除；不将它计为已迁移 | `server.py`、`game_lifecycle.py` |

## 查询、间接与非 MCP 入口

39 个查询已逐一对应到 GameState／Lua 查询构造器。名称清单保留在下表，避免只审计显眼的提交工具。

| 查询组 | 工具 |
| --- | --- |
| 静态知识 | `search_knowledge`、`get_knowledge` |
| 世界、单位与地图 | `get_game_overview`、`get_units`、`get_spies`、`get_cities`、`get_city_production`、`get_map_area`、`get_settle_advisor`、`get_pathing_estimate`、`get_global_settle_advisor`、`get_builder_tasks`、`get_empire_resources`、`get_strategic_map`、`get_turn_status` |
| 治理、宗教与伟人 | `get_tech_civics`、`get_policies`、`get_notifications`、`get_governors`、`get_unit_promotions`、`get_city_states`、`get_pantheon_beliefs`、`get_religion_beliefs`、`get_dedications`、`get_great_people`、`get_gp_advisor`、`get_victory_progress`、`get_religion_spread` |
| 外交、商路、建造与存档 | `get_diplomacy`、`get_pending_trades`、`get_pending_diplomacy`、`get_trade_options`、`get_trade_routes`、`get_trade_destinations`、`get_district_advisor`、`get_wonder_advisor`、`get_purchasable_tiles`、`get_world_congress`、`list_saves` |

查询会更新观察缓存、安装外交监听器，`list_saves` 会更新索引映射并可能发起原生枚举；这些属于观测／接口状态。未发现这些已注册查询主动派遣单位、购买、回应外交或投票。MCP 目前按 get_/list_ 名称放行查询；后续新增工具必须重新核对语义，前缀不是纯度证明。`get_religion_beliefs`、`get_dedications` 缺 readOnlyHint，但当前实现是查询。

非 MCP 路径也属于审计范围：

- GameState 的 `vote_world_congress`、`submit_congress` 仍是直接提交，未注册 MCP；不可拿它们绕过显式票计划。`test_trade` 是 `propose_trade(test_only)` 的内部路径。
- 多个 GameState 直接方法依赖 MCP 门禁，自身没有 `_action_gate`，例如 sleep、research、purchase_tile、GP 和 religion。当前保证是串行 MCP 调用；直接嵌入 GameState 的调用者仍须逐步补齐入口约束。
- `scripts/integration_turns.py` 使用 stdio MCP；`scripts/test_game_state.py` 直接使用 GameState，因此不天然拥有 MCP 全局调度保障。`test_connection.py`、`test_queries.py` 会连实机但当前读取示例没有业务变更。这些脚本不属于本次离线测试。
- 宣战后的 `_cleanup_war_diplomacy` 是后台延迟变更；其他 observer 仅记录事件。原生游戏自身的自动回合／事件效果不是另一个 MCP 提交入口，但必须作为确认时的竞争来源考虑。

## 使者试点契约

`send_envoy(player_id)` 的 interface 不变。提交前读取当前世界；紧贴原生命令再次检查世界、回合、玩家、城邦身份、已相遇、资格、可用使者及 GameCore 空闲。Python 在进入可能提交的 await 前保存 pending。Lua 在同一块内保存请求 token、双方计数、玩家和目标城邦身份，然后仅调用一次标准 GIVE_INFLUENCE_TOKEN。

确认必须同时具备：匹配的原生记录、原生命令正常返回、世界／回合／玩家一致、目标身份一致、GameCore 空闲、可用使者恰好减少一枚、目标收到的使者增加。目标增量可大于一，保留原生政策和文明加成。不猜测 InfluenceGiven 的事件参数，也不以单侧资源下降确认目标。

丢回应、超时、取消、原生异常、证据不全都保留 pending。同目标重入只核对原请求；不同目标和其他 MCP 变更先确认原请求；静态／游戏查询以及显式 popup／restart 恢复路径沿既有规则工作。观察到世界不匹配后锁存失效，即使回到原身份也不释放。确认完成后，下一次独立调用才代表新使者。

范围更新（2026-10-19，用户明确）：单一 MCP 控制游戏，人工或第三方并发操作不纳入设计和验收。使者采用请求记录与双侧状态变化确认；额外消费导致计数不符仍保持未知。MCP 服务进程重启后的意图持久化另属运行边界，不在本轮改造中扩展。

原生来源：本机安装包 `Assets/Base/Assets/UI/PartialScreens/CityStates.lua` 的 `OnConfirmPlacement`（748–759 行）使用 PARAM_PLAYER_ONE＋GIVE_INFLUENCE_TOKEN，界面通过 GetTokensToGive／GetTokensReceived、CanGiveInfluence／CanReceiveInfluence／CanGiveTokensToPlayer 判断和展示；未从刷新回调推断事件参数。

## 分批工作项与验收门槛

| 顺序 | 工作项 | 完成条件／状态 |
| --- | --- | --- |
| 1 | 全量入口与隐藏副作用清单；使者试点 | 本文清单与代码已完成；Lua 5.1 故障注入、MCP 门禁和相关回归见[验收记录](../acceptance/2026-10-19-envoy-lifecycle.md)；实机未验证 |
| 2 | 消费与不可逆动作：purchase_item／tile、总督头衔、伟人招募／赞助、升级／删除、占城决定；save 取消缺口 | 待实施。每个动作分别绑定原对象，证明两侧效果；save 沿既有生命周期修复，不复制普通动作模块 |
| 3 | 目标状态与单位动作：科研／市政、政策／政体、总督指派、readiness、建造／维修、商路／间谍 | 待实施。删除根据一次旧读数而自动换通道重试的路径；期望状态已满足时可明确返回 no-op |
| 4 | 多步骤协议：宗教创建、外交关系、议会；后台宣战清理；直接 GameState 门禁 | 待实施。保留部分进度和原会话／世界，禁止整段重发；最后消除入口绕行，逐项回归已有 owner |
| 5 | 实机与运行边界 | 待实机验证。从 `benchmark_turn_1` 建立独立测试轮次，达成有使者且已相遇城邦条件后验证一次消费与原生加成；保留存档和证据。不将离线通过等同实机通过 |

后续每项以“原生回读能证明什么”决定 implementation，不能把使者计数比较复制到所有动作。已有所有者继续保留，逐项迁移；不因清单数量而先造统一抽象。

## 后续正式规格

用户已要求按 to-spec → to-tickets → implement-spec 推进。正式[规格](../../.scratch/mutation-lifecycle/spec.md)和[逐票依赖图](../../.scratch/mutation-lifecycle/map.md)接续本次审计；后续实施状态以逐票记录为准。
