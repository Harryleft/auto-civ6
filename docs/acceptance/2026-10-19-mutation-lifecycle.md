# 全量状态变更生命周期覆盖与离线验收

2026-10-19；基线 `d3450a3`，按[批准规格](../specs/2026-10-19-mutation-lifecycle/spec.md)和[18 票任务图](../specs/2026-10-19-mutation-lifecycle/map.md)完成全部实施。最终离线回归 **3856 passed in 127.55s**；冻结集成 `c64201e` 的 Standards／Spec 独立复核均为 **No material issues found**。18 票及规格已 resolved，18 个实施工作树已安全清理，分支保留。

[原始审计](../specs/2026-10-19-mutation-audit.md)保留迁移前的事实；本页记录迁移后的覆盖和证据，不用历史实机记录代替本次验证。

## 入口口径与所有者

源码注册与公开 MCP 工具目录均为 **82 个入口：43 个语义变更工具、39 个纯查询**。`get_religion_beliefs`、`get_dedications`、`get_gp_advisor` 补齐 `readOnlyHint`；其他入口及参数名称保持兼容。下表列全 43 个变更工具，组合工具的每个业务分支另列。

| MCP 工具 | 结果所有者与确认边界 |
| --- | --- |
| `unit_action` | ActionLifecycle；移动、攻击、建城保留原证据；其余分支见下表 |
| `spy_action` | ActionLifecycle；旅行／任务确认精确指派启动；逃离确认原间谍的选择门禁解除 |
| `city_action` | ActionLifecycle；城市射击保留原证据；keep／reject／raze／liberate_founder／liberate_previous 绑定原完整城市和 captured／rebelled 来源 |
| `skip_remaining_units` | ActionLifecycle；只读枚举候选，逐单位提交 skip，未知立即停止批次 |
| `promote_unit`、`upgrade_unit` | ActionLifecycle；原单位晋升／stored 清理／回血阶段；升级核对原生真实目标、金币和资源费用 |
| `send_envoy` | ActionLifecycle；原城邦收到使者增加与可用使者减少 |
| `appoint_governor`、`promote_governor`、`assign_governor` | ActionLifecycle；精确任命／技能与头衔消费，指派核对原城市与 assigned city；建立进度属于后续过程 |
| `set_research` | ActionLifecycle；tech／civic 两个分支均核对实际目标，无 GameCore fallback |
| `change_government`、`choose_dedication` | ActionLifecycle；当前政体或可验证的无政府期启动；原时代中的实际承诺 |
| `set_policies` | ActionLifecycle；保存完整槽位计划，付费解锁与换卡分阶段，已付费或已进入阶段不重放 |
| `choose_pantheon`、`found_religion` | ActionLifecycle；精确万神殿；创教、第一信条、第二信条的逐阶段回执与最终集合 |
| `set_city_production`、`set_city_focus` | ActionLifecycle；原完整城市的生产队列／准确区域位置，或真实 Citizens 产出偏好 |
| `purchase_item`、`purchase_tile` | ActionLifecycle；指定单位／建筑／地块与实际币种消费，旧对象不冒充本次购买 |
| `recruit_great_person`、`patronize_great_person`、`reject_great_person` | ActionLifecycle；原 individual、实际点数／金币／信仰；招募／赞助核对新完整单位，拒绝不处理池中下一位 |
| `send_diplomatic_action`、`propose_peace`、`form_alliance` | ActionLifecycle 的关系协议；准确方向／类型关系、原费用及原会话阶段。`OPEN_BORDERS` 转交已有交易所有者，原别名重入仍交该所有者匹配 |
| `propose_trade`、`respond_to_trade`、`negotiate_incoming_trade`、`respond_to_diplomacy` | DiplomacyProgress；主动交易、反提案、来约最终接受／拒绝、INSPECT／EQUALIZE、原语句回应与会话终结 |
| `open_world_congress`、`queue_wc_votes` | CongressProgress 保存原意图；Lua 保存原生分项投票、提交和继续阶段。只报告本阶段结束／分配请求，不声称实付 Favor 或获胜 |
| `end_turn` | TurnProgress；原世界、起始回合和原差分快照，确认回合增长及本地就绪或原生终局 |
| `dismiss_popup` | 显式 UI 生命周期及 DiplomacyViewCleanup；原生界面回调、信息弹窗、议会 review 与严格资格已消失的 stale 晋升通知清理 |
| `run_lua` | 受全部未决门禁约束的显式调试入口；不能从任意代码推导业务意图或完成证据 |
| `save_game` | 显式保存生命周期；SaveComplete、新文件指纹、保存前后身份／回合与检查点记录 |
| `load_save`、`load_game_save`、`reload_game_save`、`load_save_from_menu` | 单一显式替换所有者；索引只映射已展示名称，完整检查点身份与就绪／快照验证 |
| `kill_game`、`launch_game` | 原生进程生命周期；普通调用遵守未决门禁，已确认关闭才退役旧会话 |
| `restart_and_load` | 唯一跨未决门禁的显式故障恢复；必须指定已确认检查点，不猜 autosave |

## 组合与隐藏路径

| 分支或间接副作用 | 当前约束 |
| --- | --- |
| `unit_action`: move／attack／fortify／skip／found_city | MCP 保留完整 ID；旧协议按原意图先匹配再确认。完整 ID 复用在提交前拒绝；移动回读拒绝替代单位 |
| cancel_move／pillage／sacrifice_charges | 保留已有原生队列／损坏／项目贡献证据与单次提交；奖励只报告实际测量范围 |
| automate／heal／alert／sleep | 精确 UnitOperationStarted／Added 与就绪／活动证据；不凭 `ready=false` 认定任意任务，不修改移动力 |
| improve／repair／remove_improvement／remove_feature／build_route | 原地块效果与次数／资源共同确认；资格 false 不盲发 repair；道路使用原生表费用，不硬编码路线 index |
| trade_route／teleport | 原商人、目标完整城市及 native 路线／转移结果；不自动取消或改投其他目标 |
| delete | 本次 entered／returned、精确 UnitRemovedFromMap 和原单位 fresh absence；不访问已销毁 userdata |
| activate／spread_religion | 原 individual 激活与次数、预言家创教准备、或原宗教压力和 spread charge；不把多数宗教未改变当作失败 |
| spy travel／mission／escape | travel／mission 返回 `SPY_ASSIGNMENT_STARTED`，允许后续推进；escape 仍要求活动且未关闭的精确通知，才可读取危险的 GetNextEscapingSpyID |
| `propose_trade(test=true)` 与直接 `test_trade` | 评估会打开原生会话／工作草案并发送 INSPECT，属于 DiplomacyProgress 变更，不当作纯查询 |
| `negotiate_incoming_trade(mode=inspect/equalize)`、来约 accept/reject | 同一 IncomingAttempt 绑定世界、会话、双向条款及资格阶段；最终响应不再绕过所有者直发 |
| 政策付费、宗教三阶段、关系多阶段 | 原调用或同意图显式重入只继续未进入阶段；普通确认、查询和其他动作门禁只读 |
| 晋升通知、宣战后的视图清理 | 晋升不扫描并修改全军 stored；宣战不留延时后台清理。使用已有显式 dismiss_popup，未知资格或新会话不强行清理 |
| 原议会 handler 的投票／submit／ACTION_ENDTURN | 原生阶段所有者保留；完整票单 REGISTERED 后仅显式 end_turn 可触发，观测必须新鲜；不套普通动作严格同回合条件 |
| 非 MCP 的 `vote_world_congress`／`submit_congress` | 不再直发，明确要求完整 `queue_wc_votes` 计划 |
| 列表／查询中的只读观察器 | 只记录原生事实，不创建交易、选择逃离路线、投票或自动推进业务阶段 |
| 信息弹窗原生 callbacks 与任意 Lua | UI／调试边界单列，不能声称具备任意业务消费的完成证明；未知业务状态仍要求显式恢复 |

普通动作的未知、取消、回读失败、空回应或原生进入后异常都保留原意图。所有 scoped pending 一旦读到任一有效的世界、回合或玩家字段与原值不匹配，就锁存失效；其他字段未知不能遮蔽该变化，回到原身份不能追认。身份 getter 抛错、缺失或返回无效值只阻止当次确认；范围完整有效且匹配、原提交与领域效果证据仍完整时，可只读确认原请求。遗留低 index 的直接单位接口保持兼容，面向 MCP 的完整 ID 不再被截断。城市查找和生产回读按原完整 ID 核对。

## 门禁与恢复

直接 GameState 与 MCP 使用相同的许可边界：save／load 未决时只允许查询和已确认检查点恢复；外交、普通动作、推进和议会未决时，先交原所有者匹配意图。相同意图的本次调用在确认原请求后结束，下一次独立调用才可提交新请求。普通新动作、保存、换档、任意 Lua 和普通启动／关闭进程不能绕过门禁。

外交响应、来约处理、议会打开／完整票计划及显式弹窗清理保留各自解除阻塞的通路。`end_turn` 是唯一能对最新可信 REGISTERED 议会请求使用 `allow_registered_turn=True` 的入口。未放宽 pending-turn 下的 spy_action：原生材料仅证明逃离通知是阻塞项；现有推进可先确认新回合就绪，再显式选择逃离，不据此猜测同回合例外。

保存结果未知仍使用 `save_pending` 和显式检查点恢复；重复保存不会重新写入，也没有新增自动补记或跨进程恢复事务。已确认文件与检查点资格不同，部分／取消／身份不明的新保存不能用于恢复。运行模型是单一 MCP 控制，未增加人工并发、第三方仲裁、后台轮询或跨服务重启的意图持久化。

## 验证记录

18 的新增公开入口回归位于 [test_mutation_entry_audit.py](../../tests/test_mutation_entry_audit.py)，使用真实 GameState、ActionLifecycle、MCP 调度和实际生成的 Lua。仅原生引擎、连接、临时存档目录和进程 adapter 被模拟；推进组合门禁复用既有 GameStateConn 的原生协议 adapter。

红灯证据：完整身份／来源锁存首轮 11 例失败；跨所有者门禁和旧 skip 重入首轮 21 例失败；公开工具目录在三项查询缺少 readOnlyHint 时失败；兼容低 index 的移动被替代单位到达目标误确认，以及 OPEN_BORDERS 别名无法重入原交易，也各自先复现失败再修复。修复后新增 63 项通过。旧入口假数据中两项 low-ID 期望更新为 full-ID；旧直接 kill_game 绕过推进的期望改为与 MCP 一致阻止，恢复仍交已确认检查点。

初审冻结前全量离线回归：**3563 passed in 124.08s**，覆盖 `tests/`，其中本票新增 63 项。此前相关回归为 249 项身份与旧动作测试、497 项门禁／推进／保存／议会／交易测试。`compileall -q src tests`、基线至 HEAD 的 `git diff --check` 通过；26 个修改的 Markdown 文件中 156 个本地链接／锚点全部可解析。源码注册与公开工具目录逐名一致，43 项变更入口在覆盖表中遗漏为 0。

初始实施提交 `3fa68cd`，基于已集成 01–17 的 `b219479`；初审冻结集成 `7e3a2a9`，首轮修复 `22b5653`，首次复核冻结集成 `ba9cdd8`。下文分别保留两轴审查与修复历史；各轮待复核描述均为当时状态。最终受测代码 `31ae3f5` 已合入 `c64201e`，双轴复核、闭票及工作树清理均已完成。

```bash
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest tests/ -q
~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m compileall -q src tests
git diff --check
```

本次仅离线验证，未连接 FireTuner，未运行 scripts 下实机脚本，未启动／关闭／换档真实游戏，未 push。各票原生依据及领域故障覆盖保留在[工作项](../specs/2026-10-19-mutation-lifecycle/map.md)的 Answer 中。

## Standards

对冻结集成 `7e3a2a9` 的初审发现 **1 个 P2、0 个独立 smell**：13 个新增 Lua 领域文件把暂时未知的身份、时代或政策布局读数当作有效变化，永久锁住本可确认的原请求。依据为 [AGENTS 约束](../../AGENTS.md#必读约束)的“区分不可见、未知和不存在”，以及[架构约束](../architecture.md)中“见到不匹配后锁存失效”的条件。重复判断属于该根因的 possible Duplicated Code，不另计发现。

修复将身份的有效事实／未知／实际不匹配判断集中在已有 `_helpers.py` 的小型 Lua 片段；时代和政策布局先校验事实再比较。复核同类旧路径后，一并覆盖 `envoys.py`、`contribution.py`，合计 15 个领域文件。各领域效果、费用、完整身份与原生事件要求继续在本域确认；缺少唯一事件证据时仍保持未知。

首轮修复的 TDD 红灯分别为地块购买 3 例、其余共享范围守卫 36 例、时代与政策布局 4 例。该轮新增 [152 项公开入口回归](../../tests/test_scope_observation_recovery.py)，使用真实 GameState／MCP 和实际 Lua，仅在原生 adapter 注入 getter 抛错、nil、缺失、非预期类型或 NaN。已有结果证据完整时，恢复读数可确认且提交数保持 1；有效玩家／文明／seed／回合、时代或政策布局变化仍锁存。政策已付款时，只读恢复不换卡，同意图续行只提交一次换卡，金币余额保持 860。

首轮相关回归 **1231 passed in 15.15s**，日志 `/tmp/civ6-review-scope-related.log`；该轮全量 **3715 passed in 125.17s**，日志 `/tmp/civ6-ticket18-review-fix-full.log`。`compileall -q src tests` 和 `git diff --check` 通过；26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。Standards 对 `ba9cdd8` 的首次复核已关闭原 P2，**当轮 0 个剩余问题**。

对第二轮修复后的冻结集成 `2643c1d`，Standards 原 P2 保持关闭，新增 **1 个 P2**：公共外层身份探针只在四字段全部有效时输出事实，未知字段会掩盖另一个有效变化。没有内层身份守卫的时代承诺可在 seed 未知、回合已变化后，因原值恢复而错误确认旧请求。

本轮修复逐字段隔离 getter 异常、验证原生类型并保留有效事实；确认按原绑定玩家读取文明。Python 先锁存任何有效失配，身份全部有效且匹配才消费领域完成证据；准备提交仍要求完整身份。普通身份刷新复用同一探针，将完整 GAME_SCOPE 交当前动作比较，保留 GAMESEED 与二元身份返回接口；世界身份不完整时不更新缓存，有效玩家／回合变化也会锁存。本轮新增 **56 项**公开 GameState／MCP＋真实 Lua 回归：混合失配 24 项、纯未知恢复 20 项、普通身份刷新 12 项。红灯为原 seed／回合复现 2 例、身份刷新文明／seed 组合 6 例、未知玩家与变化文明 2 例，再补普通刷新玩家／回合组合 6 例；全部验证单次提交。针对回归 **574 passed in 13.48s**，日志 `/tmp/civ6-review-third-targeted-final.log`。当时新增 P2 的修复待独立 Standards 复核。

最终 Standards 复核固定 `c64201e`，差异为 `2643c1d...c64201e`，结论 **No material issues found**：本轮关闭最后一个 P2，遗留 0、新增 0；累计发现的两个 P2 均已关闭。独立运行 8 组 ChoiceConn 组合：`confirm`／`get_game_identity` × seed nil／throw × 回合不变／变化。读数恢复后，未发生有效变化的请求可以确认，发生有效变化的请求仍失效；全部原生提交数为 1。

## Spec

对初审冻结集成 `7e3a2a9` 的结论为 **No material issues found（0）**。对首轮修复后的 `ba9cdd8` 独立复核发现 **2 个 P2**：外层 ACTION_IDENTITY 将无效文明／回合等当成有效变化；政策布局虽检查整数，却仍将负槽数、不存在的槽位类型当成有效变化。两者都会在读数恢复后继续锁住原请求。

本次修复统一外层准备与确认的身份解析，普通身份刷新也先验证原生文明与 seed 再影响未决状态或缓存。原生数值在序列化前检查类型，避免字符串 `"42"` 被误当成数字 42；外层与 Lua 记录都要求玩家为 0–63、回合非负，seed 保留负整数语义。政策槽数要求非负，槽位类型与政体必须存在于原生目录；政体 `-1` 保留原生“无政体”的有效语义。时代必须存在于原生 Eras 目录。

该轮新增 **85 项**公开入口回归，范围恢复测试累计 **237 项**。红灯包括外层首读 2 例、身份刷新与数字字符串 4 例、政策／时代值域 6 例、玩家 64 的准备／确认 2 例。验证同时保留有效不匹配锁存、未知玩家不掩盖有效回合变化、玩家 0／63 与负／零 seed 可正常完成，以及原政策单次付款和显式续行。针对回归 **604 passed in 13.13s**，日志 `/tmp/civ6-review-second-targeted.log`。第二轮修复 `21ff615` 合入冻结集成 `2643c1d` 后，Spec 独立关闭这两个 P2，**新增 0**；当时 Standards 新发现的修复待两轴独立复核。

本轮全量首轮为 3799 通过／1 失败：旧皇家学会贡献测试依赖 `local actionMe =` 源码前缀识别确认帧，统一探针后未注入预期的丢帧。adapter 已改为识别实际返回的 ACTION_IDENTITY 帧，保留原行为断言；相关 **367 passed in 3.71s**，日志 `/tmp/civ6-review-second-contribution.log`。

本轮最终全量 **3800 passed in 125.59s**，日志 `/tmp/civ6-ticket18-second-review-full.log`。`compileall -q src tests`、`git diff --check` 通过；当时 26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。该阶段代码只完成实现者验证，后续独立复核结果分别保留。

最终 Spec 复核固定 `c64201e`，差异为 `2643c1d...c64201e`，结论 **No material issues found**：此前两个 P2 保持关闭，本轮新增 0。独立运行新增 56 项公开入口真实 Lua 测试，**56 passed in 1.26s**；原意图、已提交阶段和一次提交约束保持。

## 最终验证与清理

范围恢复回归累计 **293 项**，最后一轮代码修复限于公共动作身份读取／比较与普通身份刷新。最终全量 **3856 passed in 127.55s**，日志 `/tmp/civ6-ticket18-third-review-final.log`。`compileall -q src tests`、`git diff d3450a3 HEAD --check` 与工作区差异检查通过；最终源码与测试的 Git tree 均与受测 `31ae3f5` 完全一致。闭票仅改文档，复用全量结果；最终 26 个 Markdown 文件／160 个本地链接及锚点检查，0 unresolved。

18 个实施工作树均未使用 force 安全移除，分支保留，`git worktree list` 仅主检出；清理日志 `/tmp/civ6-mutation-worktree-cleanup.json`。01–18 与规格全部 resolved，无剩余验收项；当时未跟踪的分享稿已保留，现归档于[团队分享资料](../../games/benchmark-sumeria-719665280/runs/2026-10-18-t233/team-sharing/README.md)。未 push、未操作实机。
