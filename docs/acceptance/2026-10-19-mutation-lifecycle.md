# 全量状态变更生命周期覆盖与离线验收

2026-10-19；基线 `d3450a3`，按[批准规格](../../.scratch/mutation-lifecycle/spec.md)和[18 票任务图](../../.scratch/mutation-lifecycle/map.md)实施。01–17 已合入；18 完成入口收口及离线验证后交双轴审查，闭票与工作树清理由最终集成环节确认。

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

普通动作的未知、取消、回读失败、空回应或原生进入后异常都保留原意图。所有 scoped pending 一旦见到世界、回合或玩家不匹配，就锁存失效；回到原身份不能追认。遗留低 index 的直接单位接口保持兼容，面向 MCP 的完整 ID 不再被截断。城市查找和生产回读按原完整 ID 核对。

## 门禁与恢复

直接 GameState 与 MCP 使用相同的许可边界：save／load 未决时只允许查询和已确认检查点恢复；外交、普通动作、推进和议会未决时，先交原所有者匹配意图。相同意图的本次调用在确认原请求后结束，下一次独立调用才可提交新请求。普通新动作、保存、换档、任意 Lua 和普通启动／关闭进程不能绕过门禁。

外交响应、来约处理、议会打开／完整票计划及显式弹窗清理保留各自解除阻塞的通路。`end_turn` 是唯一能对最新可信 REGISTERED 议会请求使用 `allow_registered_turn=True` 的入口。未放宽 pending-turn 下的 spy_action：原生材料仅证明逃离通知是阻塞项；现有推进可先确认新回合就绪，再显式选择逃离，不据此猜测同回合例外。

保存结果未知仍使用 `save_pending` 和显式检查点恢复；重复保存不会重新写入，也没有新增自动补记或跨进程恢复事务。已确认文件与检查点资格不同，部分／取消／身份不明的新保存不能用于恢复。运行模型是单一 MCP 控制，未增加人工并发、第三方仲裁、后台轮询或跨服务重启的意图持久化。

## 验证记录

18 的新增公开入口回归位于 [test_mutation_entry_audit.py](../../tests/test_mutation_entry_audit.py)，使用真实 GameState、ActionLifecycle、MCP 调度和实际生成的 Lua。仅原生引擎、连接、临时存档目录和进程 adapter 被模拟；推进组合门禁复用既有 GameStateConn 的原生协议 adapter。

红灯证据：完整身份／来源锁存首轮 11 例失败；跨所有者门禁和旧 skip 重入首轮 21 例失败；公开工具目录在三项查询缺少 readOnlyHint 时失败；兼容低 index 的移动被替代单位到达目标误确认，以及 OPEN_BORDERS 别名无法重入原交易，也各自先复现失败再修复。修复后新增 63 项通过。旧入口假数据中两项 low-ID 期望更新为 full-ID；旧直接 kill_game 绕过推进的期望改为与 MCP 一致阻止，恢复仍交已确认检查点。

最终全量离线回归：**3563 passed in 124.08s**，覆盖 `tests/`，其中本票新增 63 项。此前相关回归为 249 项身份与旧动作测试、497 项门禁／推进／保存／议会／交易测试。`compileall -q src tests`、基线至 HEAD 的 `git diff --check` 通过；26 个修改的 Markdown 文件中 156 个本地链接／锚点全部可解析。源码注册与公开工具目录逐名一致，43 项变更入口在覆盖表中遗漏为 0。

实施提交 `3fa68cd`，基于已集成 01–17 的 `b219479`。18 保持 claimed；Standards／Spec 双轴审查、最终闭票和工作树清理待集成环节执行。

```bash
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest tests/ -q
~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m compileall -q src tests
git diff --check
```

本次仅离线验证，未连接 FireTuner，未运行 scripts 下实机脚本，未启动／关闭／换档真实游戏，未 push。各票原生依据及领域故障覆盖保留在[工作项](../../.scratch/mutation-lifecycle/map.md)的 Answer 中。
