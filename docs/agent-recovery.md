# Civ 6 游戏恢复与崩溃预防

## MCP 自动存档

`end_turn` 每回合自动保存为 `0_MCP_NNNN`，保留最新 5 个。它们是首要恢复点。

按名称加载是首选，不需要先调用 `list_saves`：

```text
load_game_save("0_MCP_0079")
get_game_overview
```

对局内 Lua 加载通常约 5 秒；若需要回到主菜单恢复，默认通过 Civ VI FrontEnd API 加载并完成内置“继续游戏”，不依赖 GUI 点击或视觉识别。加载后必须用 `get_game_overview` 验证回合和局面。

## DSH 启动自动恢复（显式 opt-in）

默认关闭。需要时从 `civ6-mcp` 根目录执行：

```bash
CIV_MCP_DSH_AUTO_RESUME=1 ./scripts/deepseek_harness web
```

一键启动脚本 `scripts/civ6_launch` 默认就带该 opt-in（`--no-resume` 关闭）；
它还会在 DSH 启动前代为拉起游戏并等待 FireTuner 监听。

MCP 启动时先通过 FireTuner 判断是否已经在对局；若只到主菜单或游戏尚未启动，则复用 GUI 的“单人游戏 → 加载游戏 → Continue”流程。若启动时游戏仍在启动过程中（FireTuner 可达但 `MainMenu` Lua state 尚未出现），会自动等待主菜单就绪（最长 120 秒）再载档，避免与冷启动竞态；超时未就绪则放弃并保持游戏原样。恢复点优先选择最新的 `0_MCP_*.Civ6Save`，没有可用 MCP 存档时才回退到 `AutoSave_*.Civ6Save`。该路径与 eval 专用的 `CIV_MCP_SAVE_FILE` 自动启动完全分离，绝不清理 `0_MCP_*`；FireTuner 仍只允许一个客户端。

## AI 回合卡住

```text
restart_and_load("0_MCP_NNNN")
get_game_overview
```

**恢复是独立一步，`end_turn` 自己绝不重启游戏。** 挂起时 `end_turn` 返回
`HANG:<回合>:<存档>` 加一条明确的下一步说明（`HANG_RECOVERY_IS_A_SEPARATE_STEP`），
并且不会诱导重发。之所以这样拆：以前 `end_turn` 会在自己内部最多重启三次，
结果一次调用的期限必须覆盖「等待 + 三次重启」（约 50 分钟），而且调用方再也分不清
这一回合只是慢，还是卡死过并被重启过。现在单次调用只需要覆盖一轮回合。

**议会回合永远不会被判成挂起。** 议会界面挂着时回合号不动，而阻塞项查询在议会期间
返回空——过去的判定逻辑因此把「没人投票」误判成「AI 卡死」，进而杀掉并重启一个完全
健康的游戏，最多三次。现在这种情况返回
`CONGRESS_NOT_DRIVEN`（带 `CONGRESS_NOT_DRIVEN_IS_NOT_A_HANG`），
明确禁止重启，并给出下一步：`get_world_congress` → 用决议类型名注册 `queue_wc_votes`
→ 重新 `end_turn`。

`restart_and_load` 会结束进程、重新启动，并借助共享 FireTuner 连接在主菜单调用 FrontEnd API 加载存档，通常约 90 秒。不要在 FireTuner 仍有旧客户端时并行启动恢复流程。

默认拒绝 OCR/GUI 菜单回退：它可能误点到其他窗口，也不能作为无人值守验证的可靠依据。仅在明确接受该风险时设置 `CIV_MCP_ENABLE_OCR_RECOVERY=1`；若 FrontEnd API 不可用且未设置该变量，恢复会返回明确错误而非尝试视觉操作。

如果误加载了 T1 场景存档而不是自动存档，`end_turn` 会发出 CRITICAL 警告并指出正确的自动存档名称。

其他恢复工具：`list_saves`、`load_save(index)`、`kill_game`、`launch_game`、`load_save_from_menu(name)`。存档名称不带 `.Civ6Save` 扩展名，例如使用 `AutoSave_0221`。

## 崩溃根因（JobSet use-after-destruction，单一崩溃类）

经 5 份崩溃报告（.ips）字节级对比确认：`Civ6_Exe_Child` 反复 SIGABRT，栈上 `__cxa_pure_virtual`，责任线程为 TBB worker（WinID 6/7/9 轮换）。**这是单一崩溃类，不是随机崩溃**：

| 崩溃时间 | 触发线程 | 进程存活 | Civ6 偏移 | libtbb 帧 |
|---|---|---|---|---|
| 8/13 22:49 | TBB WinID 6 | 49.8 min | +0x8cbbec | +0xc458→0xc580→0x11f14→0x126ac→0x18eb0 |
| 8/13 23:04 | TBB WinID 6 | 13.1 min | +0x8cbbec | 同上 |
| 8/13 23:25 | TBB WinID 7 | 18.2 min | +0x8cbbec | 同上 |
| 8/14 15:11 | TBB WinID 7 | 3h43m | +0x8cbbec | 同上 |
| 8/14 17:21 | TBB WinID 9 | 1h44m | +0x8cbbec | 同上 |
| 8/15 00:15 | TBB WinID 12 | ~25 min | +0x8cbbec | 同上（P1 验收中实录，第 6 份） |

第一性原理判断（已被数据证实）：**并行任务访问了生命周期已结束的 C++ 多态对象**（stale task / use-after-destruction）。证据：
- 6 次崩溃**同一可执行文件偏移 +0x8cbbec**、同一组 TBB 偏移——确定性代码路径，不是内存随机性
- 触发线程从 WinID 6→7→9→12 轮换——谁抢到悬垂 task 谁崩
- 进程存活 13 分钟到 3.7 小时差异巨大——**与会话时长无关**

符号级定位（`nm`/`atos`）：
- 崩溃点 `0x8cbbec` 位于 `Platform::JobManager::SpawnList` (0x8caf0c) 之后 0xce0 字节——JobManager 区域末尾未命名内联代码（worker 从 JobList 取 task 执行的内联路径）
- 附近符号 `Localization::String::Empty`——任务可能涉及本地化文本（AI 回合中单位/城市名渲染）
- 链路：**TBB worker → 从 Civ6 JobList 拉取 task → task 虚方法 execute() → 对象已析构（vtable 清空）→ `__cxa_pure_virtual` → abort**

**结论：引擎存在 JobSet/JobList 生命周期管理 bug，外部无法修补闭源二进制。** 崩溃发生在 AI 回合处理期（TBB 并行任务最活跃时），与特定回合数/动作无确定关联。恢复成本已最小化：end_turn 自动存档（0_MCP_NNNN）在崩溃前完成，重启加载损失 0 回合。

## 崩溃规避（2026-08-15 新增：已验证方向）

崩溃概率与 TBB worker 数量正相关（谁抢到悬垂 task 谁崩，worker 越多竞态窗口越大）。规避按性价比排序：

1. **引擎线程上限（已自动化）**：`AppOptions.txt [Performance] MaxJobThreads` 由 `-1`（每核一个 worker）改为 `4`。恢复链每次拉起游戏前由 `_ensure_job_thread_cap()` 幂等确保（CRLF 安全、首备份 `AppOptions.txt.civ6-mcp.bak-<date>`）；`CIV_MCP_MAX_JOB_THREADS=0` 可关闭。与社区结论同向：reddit "FIX: Crashing on Mac (Threading Fix)"（36 帖）即线程数修复。**生效时机：下次游戏启动**。若仍崩，降到 `2` 再观察。
2. **游戏内"性能影响"选项全部最低（手动一次性）**：Apple Silicon 用户实测（gist，多人复验）：性能选项驱动的资源生成任务同样走 TBB，全最低后从"每几分钟崩一次"变为"数小时不崩"。路径：游戏内 图形设置 → 性能影响/Memory Impact → Minimum。
3. **时序规避**：AI 回合处理期（end_turn 前后数秒）是 TBB 最活跃窗口，避免密集工具调用；批量查询放回合稳定期。
4. **兜底（已加固）**：就算崩了——每回合 0_MCP 自动存档损失 0 回合；恢复链 `restart_and_load` 已修复（launch 重试 ×3、FrontEnd API 自动加载和确认、失败明确中止）。OCR 仅可通过显式开关作为最后兜底。

要点：
- 崩溃是**进程级**，FireTuner 仅在本进程中，进程死亡即断连（报 `Cannot connect to Civ 6 at 127.0.0.1:4318`）。
- **识别崩溃类**：新 .ips 报告 + 栈上 `Civ6_Exe_Child+0x8cbbec` = 同一崩溃类，无需深度分析。
- 崩溃与内存水位无因果关系；不要用 Pages free 阈值预测崩溃。

## 崩溃预防设计（风险窗口控制）

目标：把崩溃从"猝死 + 事后恢复"改为"**可预期、存档就绪、快速恢复**"。设计不新增外部依赖，全部嵌入现有回合循环与 belief/metric 机制。

### 时序：在崩溃发生前安全落盘

关键原则：**永远不要在有未结束动作、或存档落后于游玩的时点冒险**。end_turn 自动保存 `0_MCP_NNNN` 在崩溃前完成——崩溃损失最多 0 回合，这是主要保障。

### 分层响应

| 层 | 触发条件 | 动作 | 依赖审批 |
|----|----------|------|----------|
| L0 稳定 | 回合正常推进 | 不做任何事 | 无 |
| L1 警戒 | end_turn 返回 `Cannot connect` 或新 .ips 出现 | 确认存档（0_MCP_NNNN 存在）→ 按恢复流程重启 | **是** |
| L2 崩溃 | 进程已断连/4318 不再监听 | 确认存档后重启加载（见下），损失 0 回合 | **是** |

崩溃窗口特征：AI 回合处理期（TBB 并行任务最活跃时）是最高风险窗口。**避免在 end_turn 后数秒内密集调用工具**（JobSet 生命周期更替窗口）；回合稳定期再执行批量查询/操作。

### 正确的恢复流程（L2）

仅当诊断确认 4318 已释放、无残根进程时才执行，且必须先确认存档：

```text
（安全时点）end_turn                    # 确保最新回合进入 0_MCP_NNNN
kill_game                              # 杀进程，等 Steam 注销（~10s）
launch_game                            # 重新启动（进程出现 15-30s）
load_game_save("0_MCP_NNNN")           # 加载最新自动存档
get_game_overview                      # 强制回合入口，验证回合与局面
```

**禁止**在残留 Civ6_Exe 僵尸进程存在时调用 `restart_and_load`（报 `MCP error -32001`）。先 `kill_game`，再单独 `launch_game` + `load_game_save`，不要一次重启干到底。

### 工具列表同步竞态（首次调用 unknown tool）

MCP 进程（civ-mcp）重启后，DSH 客户端需要先完成 `tools/list` 同步才注册工具。`civ6.cordis.yml` 设 `reconnect: disabled`，同步在连接建立后异步进行。**重启 civ-mcp 后第一次工具调用可能报 `unknown tool`，第二次同工具调用即成功**——这是客户端注册时序，不是工具缺失。对策：
- 进程重启后第一调用用 `get_game_overview`（既符合回合入口，也自然等待同步完成）。
- 若遇 `unknown tool`，重试一次同调用即可，不要误判为工具不存在而改走其他路径。

### 周期巡航（写入回合周期）

- 崩溃识别：end_turn 报 `Cannot connect` 或 `lsof -iTCP:4318` 无监听 → 确认新 .ips（`Civ6_Exe_Child-*.ips`）→ 比对 `+0x8cbbec` 确认同一崩溃类。
- 恢复后把崩溃时点与回合写入 diary 的 `tooling` 字段，作为跨会话崩溃频率审计线。
- 使用 `record_observation` 记录崩溃事件 metric（`metrics={"crash_occurred": 1, "crash_class": "jobset_uad"}`），让 belief 预测能对崩溃频率建模。

### 审批策略约束

当前审批策略可能为 `never`（`kill_game`/`launch_game` 不再走审批门）。此时**重启只能靠用户手动确认**，或因游戏自身崩溃而被动发生。在 `never` 策略下：
- 不要反复自动尝试 `kill_game`（会被拒绝）。
- 崩溃后把信号写入 diary 与最终回复，明确提示用户"请重启游戏并加载 0_MCP_NNNN"，或请用户手动执行 `kill_game → launch_game → load_game_save("0_MCP_NNNN")` 命令序列。

### 与之配合的缓解（针对引擎 JobSet 生命周期 bug）

- 引擎 bug 无法从外部修复；能干预的是**触发概率**与**存档及时性**。
- AI 回合处理期（end_turn 后）避免密集工具调用；批量操作放在回合稳定期。
- 崩溃与 DSH 无关，不要通过改 DSH 试图"修复"引擎；能干预的是外部节奏与恢复流程。

## 传输与审计底座的失败语义（P0 加固）

底层行为由离线测试 `tests/test_connection_reliability.py` 与 `tests/test_belief_engine_p0.py` 锁定。崩溃/断线恢复时 agent 会遇到以下四类新行为：

| 场景 | 行为 | agent 处置 |
|---|---|---|
| 变异命令（move/attack/购买/end_turn 等，含 `run_lua` 的 ingame/state-index 通道）发送时连接死亡或超时未收到 sentinel | 抛 `MutationOutcomeUnknownError`（ConnectionError 子类）；命令**恰好发送一次，绝不自动重发**（超时同样视为结果未知，非普通失败） | 不要立刻重试同一动作——先 `get_units`/`get_cities` 核实动作是否已实际生效，再决定重试或放弃 |
| 查询命令超时未收到 `---END---` sentinel | 抛 `CommandTimeoutError`（LuaError 子类），携带已收部分行；不再静默返回截断输出 | 按普通错误处理并重试（查询可安全重发） |
| 决策卡在 `executing`（进程崩溃/异常逃逸/记录路径失败） | 跨回合自动回收为 `retryable`（进程重启加载、governance turn gate）；**任意时刻**可用 `cancel_action_authorization` 显式取消（迟到结果天然 no-op，不会复活授权） | 回收/取消后照常取消或重新授权执行；`end_turn` 门禁不再被永久阻塞 |
| 游戏回退到更早回合（autosave 重载/手动读档） | 事件流记录 `game.reloaded` epoch 标记：**所有读档路径都经 `pipeline._record_game_reload_epoch`**（连接恢复、`restart_and_load` 工具、end_turn 挂起恢复），手动读档另由回合回退检测捕获；旧 epoch 的未决授权被作废（`invalidated_by_game_reload`）、关联预算锁归档，**旧 epoch 的 observations 与 world_entities 一并归档**（`epoch_superseded_by_reload`）。消费授权时另按 decision 的**创建 epoch** 兜底——即使标记已落盘而作废清扫未跑完（进程在两者之间崩溃），陈旧授权也不会被消费 | 回滚后不要重用旧事实——current_metrics 已清空、回合门要求新 epoch 的新鲜 typed snapshot；先 `get_governance_brief` 重建世界图，再基于新局面重新提案。回放/重建历史时按 epoch 分组，同回合号的两套事实分属不同 epoch |

事件流完整性：JSONL 追加带 fsync；**同一账本同时只允许一个进程写入**——`bind_game` 取 `<journal>.lock` 的 `LOCK_EX|LOCK_NB`，第二个进程会明确报错而不是静默交错序列号；加载时发现损坏行（无法解析、非 dict、或破坏事件 schema——如 sequence 非数字、缺 event_type/entity.id）会原子重写为纯完好行并追加 `log.integrity` 标记（含坏行哈希与预览），后续事件不会再拼接到坏行上。若日志中出现 `log.integrity`，说明进程曾在写入中途崩溃或日志被外部损坏。注意该重写发生在**加载路径**上，正是单写者锁存在的理由：没有它，一个写入者正在追加时发生的读取竞争会被误判为数据损坏并丢弃对方刚写入的事件。

## 相关文档

- [启动与验收](agent-startup.md)
- [回合规则](agent-turn-loop.md)
- [工具与动作参考](agent-tools.md)
- [信念引擎](belief-engine.md)
- 返回 [AGENTS.md](../AGENTS.md)
