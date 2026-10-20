# 测试效益与耗时审计（2026-10-20）

受测版本：`34887fc14c4f42436fb722f3075bccca797b604f`。本次完整采集 `tests/` 的离线执行耗时，并按测试定义进行分类；没有修改正式代码、测试文件、原始基准或游戏状态。

本文保留修复前的审计基线。替身修复、构建与等待优化已完成，后续结果见[测试优化验收](2026-10-20-test-efficiency-optimization.md)；下文的失败数和待办描述对应原始受测版本。

完整基线为 **5,233 通过、2 失败，pytest 报告总耗时 150.84 秒**。最值得先优化的是重复完整构建和与断言无关的确认等待；源码形状断言的主要问题是维护脆弱，而非运行耗时。两项失败已定位到开放边界测试替身缺少当前原生接口，仍待正式修复。

## 范围与口径

- 覆盖 103 个 Python 文件：101 个测试文件、2 个辅助文件；1,886 个测试函数定义，参数展开后 5,235 个执行实例。
- 按 pytest 的 setup、call、teardown 三阶段采集，累计阶段耗时 **146.02 秒**。它与 pytest 报告总耗时的差额包含收集、报告等开销；下表按累计阶段耗时排序。
- 分析输入为当前源码、AST、实际测试结果和三组临时进程内实验。分类是全量效益初筛；重点问题作了人工复核，不等于逐项证明所有测试均能捕获故障。
- 共享模块夹具的准备时间计入首次使用它的用例，因此单例排名不直接代表该测试函数自身的计算成本。
- “未发现低效证据”表示本轮没有标记问题，不是覆盖充分、无语义重复或已完成全部变异测试的证明。
- 没有发现归一化函数名、去除函数首部文档字符串后 AST 完全相同的测试定义；语义重复仍需按故障模式分析。

## 全量分类

每个测试定义只分配一个主判定。B2 的两个 server 函数包含其他参数实例，真正等待约 5 秒的只有两个 `[action]` 实例；连同移动范围失效实例，共 3 个主要慢实例。

| 主判定 | 函数定义 | 参数展开实例 | 累计阶段秒 |
| --- | ---: | ---: | ---: |
| A0 未发现低效证据 | 1814 | 5123 | 58.335 |
| B1 重复完整构建 | 7 | 7 | 34.157 |
| B2 无关真实超时 | 3 | 8 | 15.045 |
| B3 确认轮询成本 | 40 | 71 | 9.271 |
| B4 重复CLI加载 | 16 | 16 | 29.125 |
| C1 源码形状断言 | 5 | 8 | 0.001 |
| D1 过期测试替身 | 1 | 2 | 0.089 |

B1、B2、B3 均建议保留行为断言并优化测试基础设施。C1 是覆盖方式复核候选，不能直接删除；D1 是正确性维护问题。2 个辅助文件不单独评判有效性，其运行成本已计入调用它们的用例。

完整明细：

- [按文件分类与耗时](2026-10-20-test-efficiency/files.csv)：103 行，含文件主题、通过／失败数、阶段耗时和判定分布。
- [按测试函数判断](2026-10-20-test-efficiency/test-functions.csv)：1,886 行，含位置、参数实例数、耗时、优先级及判断限制。
- [按执行实例计时](2026-10-20-test-efficiency/cases.csv)：5,235 行，保留精确参数实例和三阶段时间。
- [机器可读摘要](2026-10-20-test-efficiency/summary.json)。

## 优先处理的问题

### P0：过期开放边界替身，2 项失败

位置：[test_mutation_entry_audit.py](../../tests/test_mutation_entry_audit.py)，函数 `test_open_borders_alias_reenters_its_original_trade_owner` 的 `[False]` 和 `[True]`，失败断言位于第 256 行。

该测试仍使用通用 `TradeConn` 并仅补 `DealAgreementTypes`。当前开放边界实现还要求 `DealManager.GetPossibleDealItems` 提供与实际工作交易绑定的原生摘要；缺少接口会返回 `OPEN_BORDERS_OPTIONS_UNAVAILABLE`，无法达到它预期的成交状态。

临时实验仅将该测试模块中的连接替身换成已有 `OpenBordersTradeConn` 的完整原生摘要适配，并统一测试子类型，**2 项均通过**。因此应修复替身、保留跨入口确认和防重放断言；不应放松正式代码的原生资格检查。该实验没有写回仓库测试。

### P1：7 项测试逐例重复完整构建，累计 34.16 秒

位置：

- [test_knowledge_diplomacy.py](../../tests/test_knowledge_diplomacy.py)：4 项，19.27 秒。
- [test_knowledge_resources.py](../../tests/test_knowledge_resources.py)：3 项，14.89 秒。

每项都通过 `build(tmp_path)` 对同一已提交快照启动一次完整 `build.py`，之后进行只读 CLI 查询。业务断言保护规则来源、条件和证据分层，值得保留；重复构建的边际保护有限。

建议为这些只读检查复用本次测试会话的构建结果。修改输入快照、校验产物损坏、路径迁移或隔离性的测试仍使用独立目录。不得为了提速绕过正式查询的指纹与版本验证。

### P1：3 个实例等待真实 5 秒，累计 15.03 秒

| 位置 | 慢实例 | 基线累计秒 |
| --- | --- | ---: |
| [test_server.py](../../tests/test_server.py)，第 1175 行 | `TestSaveTools::test_direct_menu_load_cannot_bypass_unresolved_work[action]` | 5.010 |
| [test_server.py](../../tests/test_server.py)，第 1708 行 | `TestMenuReload::test_unresolved_operation_prevents_exit_or_load[action]` | 5.018 |
| [test_action_lua_execution.py](../../tests/test_action_lua_execution.py)，第 109 行 | `test_segment_observer_known_scope_change_invalidates_move_after_scope_returns` | 5.003 |

这些断言主要保护未决门禁与身份失效，不要求真实经过 5 秒。建议在测试夹具中设置短确认预算或使用受控时钟，仍检查零额外提交、未知状态和原请求保留；专门验证截止时间与取消的测试保持独立。

### P1：确认测试的轮询间隔，71 项累计 9.27 秒

位置：[test_action_confirmation.py](../../tests/test_action_confirmation.py)。模拟回应已经按顺序准备好，但部分路径仍使用正式的 0.15 秒确认轮询间隔，等待成本与待验证行为没有对应关系。

建议增加局部短预算／短间隔夹具，保留旧位置、中间状态、最终效果、提交次数和未决门禁等断言；显式验证截止时间的测试覆盖自己的时钟设置。

### P2：另外 16 项知识测试累计 29.12 秒

位置：[test_knowledge_combat.py](../../tests/test_knowledge_combat.py)、[test_knowledge_economy.py](../../tests/test_knowledge_economy.py)、[test_knowledge_victory.py](../../tests/test_knowledge_victory.py)。三者已使用模块级构建夹具，不能再说它们“每项都重建”。

进一步成本来自跨模块重复完整构建，以及多次 CLI 子进程各自加载与核验数据。可评估共享只读构建，并保留代表性完整 CLI 路径；这些文件未纳入本轮提速实验，不给出已实现的节省承诺。

### P2：5 个源码形状测试，8 个实例仅约 0.0013 秒

位置均在 [test_congress_votes.py](../../tests/test_congress_votes.py)：

| 定义行 | 函数 | 处理建议 |
| --- | --- | --- |
| 82 | `test_non_player_target_accepts_query_index_or_escaped_raw_value` | 保留转义和目标选择语义，优先用真实 Lua 参数结果验证 |
| 102 | `test_votes_for_a_paused_session_use_one_distinct_congress_stage_continuation` | 改为核对阶段继续次数，不锁定原文表达式 |
| 109 | `test_explicit_vote_plan_submits_then_continues_its_native_stage_once` | 与已有事件顺序、次数行为测试核对后迁移或合并 |
| 143 | `test_native_popup_close_only_runs_for_visible_popup_and_does_not_accept_again` | 核对实际可见性、关闭调用和零额外接受 |
| 161 | `test_congress_native_query_is_read_only_and_scope_and_no_replay_guards_precede_spending` | 迁移前保留只读、身份守卫、零重复花费等安全约束 |

无语义变化实验将生成 Lua 中的 `current and ...` 改成 `current  and ...`。第 102 行测试因原文断言失败，而同进程的议会确认、资格和提案 Lua 测试 **143 项通过**。这直接证明该字符串检查对格式敏感；不能据此推断另外四项的全部约束已经被其他测试覆盖。

## 提速实验

所有改动只注入独立 pytest 进程：两组知识测试共用一次只读完整构建；确认预算为 0.01 秒，间隔为 0.001 秒。未修改仓库中的正式代码或测试。

| 同一批用例 | 基线累计阶段秒 | 临时优化累计阶段秒 | 结果 |
| --- | ---: | ---: | --- |
| 7 项知识检查、71 项确认检查、3 个慢实例，共 81 项 | 58.46 | 10.24 | 81 项均通过 |

该子集累计阶段耗时下降约 **82.5%**；优化进程的完整 pytest 耗时为 10.67 秒。该比较来自一次顺序基线和一次子集实验，缓存及系统负载可能影响数值；不能直接宣布全量套件已提速，正式改造后需重新测量。

## 主题与耗时分布

主题按文件职责分组，用于导航和选取回归范围，不作为代码覆盖率。

| 主题 | 文件 | 测试定义 | 执行实例 | 累计阶段秒 |
| --- | ---: | ---: | ---: | ---: |
| 交易外交与商路 | 13 | 231 | 690 | 8.219 |
| 单位动作与通用确认 | 21 | 552 | 1669 | 32.060 |
| 存档与会话生命周期 | 4 | 131 | 205 | 1.550 |
| 事实解析与叙述 | 9 | 100 | 173 | 0.148 |
| 城市与治理动作 | 14 | 172 | 536 | 6.476 |
| 宗教观测与动作 | 9 | 168 | 678 | 7.958 |
| 世界议会 | 6 | 85 | 216 | 0.251 |
| FireTuner通信 | 2 | 49 | 56 | 0.279 |
| 回合推进与就绪 | 6 | 136 | 567 | 1.919 |
| MCP入口与跨入口约束 | 4 | 152 | 286 | 12.442 |
| 规则知识与构建查询 | 11 | 86 | 109 | 74.587 |
| 胜利进度观测 | 2 | 24 | 50 | 0.134 |
| 测试支持 | 2 | 0 | 0 | 0.000 |

## 最慢文件

| 文件 | 执行实例 | 累计阶段秒 |
| --- | ---: | ---: |
| [test_knowledge_diplomacy.py](../../tests/test_knowledge_diplomacy.py) | 4 | 19.270 |
| [test_knowledge_resources.py](../../tests/test_knowledge_resources.py) | 3 | 14.887 |
| [test_knowledge_combat.py](../../tests/test_knowledge_combat.py) | 5 | 11.361 |
| [test_server.py](../../tests/test_server.py) | 187 | 11.174 |
| [test_action_confirmation.py](../../tests/test_action_confirmation.py) | 71 | 9.271 |
| [test_knowledge_victory.py](../../tests/test_knowledge_victory.py) | 6 | 9.094 |
| [test_knowledge_economy.py](../../tests/test_knowledge_economy.py) | 5 | 8.670 |
| [test_action_lua_execution.py](../../tests/test_action_lua_execution.py) | 2 | 5.010 |

## 复核与留证

基线命令为项目 `.venv/bin/python -m pytest tests -q -p civ6_audit_plugin -p no:cacheprovider --durations=30 --junitxml=...`；临时插件只采集测试位置和三阶段耗时。三组实验分别是 `optimize_probe`、`adapter_probe` 和 `format_probe`，完整插件、命令辅助脚本、日志及原始计时保存在本机忽略目录 `.local/test-audit/2026-10-20/`，不纳入 Git。

结果分开保留：原始基线 2 失败；补全替身实验 2 通过；格式敏感性实验预期 1 失败、143 通过；提速实验 81 通过。临时实验通过不覆盖原始基线失败，也不是实机验收。

后续实施顺序：修复过期替身 → 消除无关真实等待 → 复用只读构建 → 复测全量耗时 → 再处理维护脆弱的源码形状断言。本次没有删除测试，没有修改生成记忆，也未操作真实游戏。
