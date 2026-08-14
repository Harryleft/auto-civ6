# Civ 6 游戏恢复与崩溃预防

## MCP 自动存档

`end_turn` 每回合自动保存为 `0_MCP_NNNN`，保留最新 5 个。它们是首要恢复点。

按名称加载是首选，不需要先调用 `list_saves`：

```text
load_game_save("0_MCP_0079")
get_game_overview
```

通过 Lua 加载通常约 5 秒，菜单回退可能约 90 秒；加载后必须用 `get_game_overview` 验证回合和局面。

## AI 回合卡住

```text
restart_and_load("0_MCP_NNNN")
get_game_overview
```

`restart_and_load` 会结束进程、重新启动并加载，通常约 90 秒。不要在 FireTuner 仍有旧客户端时并行启动恢复流程。

如果误加载了 T1 场景存档而不是自动存档，`end_turn` 会发出 CRITICAL 警告并指出正确的自动存档名称。

其他恢复工具：`list_saves`、`load_save(index)`、`kill_game`、`launch_game`、`load_save_from_menu(name)`。存档名称不带 `.Civ6Save` 扩展名，例如使用 `AutoSave_0221`。

## 崩溃根因（macOS ARM 内存泄漏）

经机读崩溃报告确认：`Civ6_Exe_Child` 反复 SIGABRT（非空指针崩溃），栈上 `__cxa_pure_virtual`，责任线程为 TBB worker（WinID 7）。直接触发条件不是某单一游戏事件，而是 **macOS ARM 移植版的长会话内存泄漏**：当系统空闲内存跌破危险水位（约 9145 页 ≈ 143MB）时，递归队列在分配失败路径上抛异常，TBB 线程错误地把纯虚函数当作可调用对象，进而 abort。

要点：
- 崩溃是**进程级**，FireTuner 仅在本进程中，进程死亡即断连（报 `Cannot connect to Civ 6 at 127.0.0.1:4318`）。
- 系统级内存被占满的罪魁是 Civ6 自身的常驻泄漏（3h+ 会话内单调上升），不是 DSH 或 belief 引擎构造的负载。
- 关键观测：**杀进程重启（`kill_game` → `launch_game` → `load_game_save`）后，Pages free 从约 9145 恢复至约 120000 以上**，证明重启能彻底释放泄漏内存。

## 崩溃预防设计（内存巡航合约）

目标：把崩溃从"猝死 + 事后恢复"改为"**可观测、可提前拦截、可安全落盘**"。设计不新增外部依赖，全部嵌入现有回合循环与 belief/metric 机制。

### 信号源与量化

每个回合在 `get_game_overview` 成功后，读取系统内存一次，作为持续指标（用 bash 的 `vm_stat`，page size 16384）：

```text
Pages free  < 30000                 → 警戒（约 <490MB）
Pages free  < 15000                 → 危险（约 <245MB，接近 9145 崩溃点）
Pages free  > 100000                → 健康
```

### 时序：在崩溃发生前安全落盘

关键原则：**永远不要在有未结束动作、或存档落后于游玩的时点冒险**。所有高危操作前，先确保最新回合已进入 `0_MCP_NNNN` 自动存档。

### 分层响应

| 层 | 触发条件 | 动作 | 依赖审批 |
|----|----------|------|----------|
| L0 健康 | Pages free > 100000 | 不做任何事 | 无 |
| L1 警戒 | 现回合 > 20 且 Pages free < 30000 | 记录 diary + 观察；把"半小时内重启"列入 planning；本回合避免一次性大批量造兵/建图/商人集中操作 | 无 |
| L2 危险 | Pages free < 15000 | 结束本回合自然存档后，主动重启（见下），把内存水位作为首要任务 | **是** |
| L3 崩溃 | 进程已断连/4318 不再监听 | 按崩溃恢复流程恢复 | **是** |

### 正确的主动重启流程（L2）

仅当诊断确认 4318 已释放、无残根进程时才执行，且必须先落盘：

```text
（安全时点）end_turn                    # 确保最新回合进入 0_MCP_NNNN
kill_game                              # 杀进程，等 Steam 注销（~10s）
launch_game                            # 重新启动（进程出现 15-30s）
load_game_save("0_MCP_NNNN")           # 加载最新自动存档
get_game_overview                      # 强制回合入口，验证回合与局面
```

**禁止**在残留 Civ6_Exe 僵尸进程存在时调用 `restart_and_load`（报 `MCP error -32001`）。先 `kill_game`，再单独 `launch_game` + `load_game_save`，不要一次重启干到底。

### 周期巡航（写入回合周期）

- **每 20 回合**内存巡航一次（与 `get_diplomacy` 等 20 回合检查合并）。
- **每 25-30 回合主动重启一次**是安全的（存档每回合自动落盘，损失最多 1 回合），优于非预期崩溃。
- 把上一轮的 Pages free 水位和重启动作写入下一回合 diary 的 `tooling` 字段，作为跨会话健康审计线。
- 使用 `record_observation` 记录内存水位 metric（`metrics={"pages_free": N, "memory_cruise": "L0|L1|L2"}`），让 belief 预测能对健康趋势建模。

### 审批策略约束

当前审批策略可能为 `never`（`kill_game`/`launch_game` 不再走审批门）。此时**主动重启只能靠用户手动确认**，或因游戏自身崩溃而被动发生。在 `never` 策略下：
- 不要反复自动尝试 `kill_game`（会被拒绝）。
- 把 L1 警戒的信号写入 diary 与最终回复，明确提示用户"建议此刻手动重启游戏"，并给出精确的 `kill_game → launch_game → load_game_save("0_MCP_NNNN")` 命令序列。

### 与之配合的缓解（针对 mácOS ARM 泄漏本体）

- 缩短单会话运行时长，避免连续数小时不重启。
- 避免长回合内集中触发大内存峰值的操作组合（同时造大量单位 + 商人编队 + 全域修路），给分配器喘息。
- 递归队列崩溃与 DSH 无关，不要通过改 DSH 试图"修复"引擎；能干预的是外部的内存水位与重启节奏。

## 相关文档

- [启动与验收](agent-startup.md)
- [回合规则](agent-turn-loop.md)
- [工具与动作参考](agent-tools.md)
- [信念引擎](belief-engine.md)
- 返回 [AGENTS.md](../AGENTS.md)
