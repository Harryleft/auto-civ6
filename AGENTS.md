# civ6-mcp（v7 Runtime Core） — 仓库入口

这是通过 FireTuner 连接正在运行的《文明 VI》的运行时与智能体仓库。
**v7 M01 重构已经删除了旧的认知栈**（`civ6_belief_engine`、`civ_mcp.server`、
belief / governance / legacy-lean play profile），当前只有一条认知主路径。

架构与边界见：

- 当前实施方案：[v7 实施决策记录](docs/v7-decision-record.md)（含已冻结决策与实测删除边界）
- Runtime Core 边界：[Runtime Core Replacement](docs/runtime-rebuild/README.md) 与 [能力清单](docs/runtime-rebuild/capabilities.md)
- 开发改动：**设计不变量**见本文下半部分

仓库有两条互不相同的任务线——**游戏回合**和**开发改动**——先判断当前属于哪一条，
再读对应文档。本文只放硬规则、完成定义和文档路由；细节一律在被路由的文档里，
不要凭本文或旧会话推断机制。

## 完成定义（先定终点，再开工）

- **开发改动**：完成的标志是**改动已落地、验证通过、已提交**，不是「第一版写完」。
  除非用户明确要求先停下来评审，否则一路做到上述状态再汇报；中途不要为
  「请确认下一步」而停。
- **游戏回合**：由 `civ_agent` 的 LangGraph 工作流完成（M10 接线后）。完成的标志是
  本回合决策已执行或显式记录为 `NEEDS_DECISION` / `UNKNOWN`，且已追加 Game Memory。
  不要停在「给出建议」上。
- 两种情况都要在回复末尾报告证据：改了什么、跑了什么验证、结果如何；做了什么、
  结果如何、哪些事项被推迟及原因。

## 硬边界

- **FireTuner 只允许一个客户端。** 不要并行运行两个 MCP 客户端、独立 `civ-mcp`、
  连接测试或第二个 DSH 会话。4318 已有已连接客户端时直接失败，不要杀进程。
- **只有一条认知主路径。** 正式路径不允许 `observe → DeepSeek → execute`：Jev 必须
  经过。禁止新代码回退到已删除的 belief / governance 逻辑，也禁止建立与 `civ_agent`
  并列的第二套决策循环（v7 §13.3「不做兼容层」）。
- **Runtime 不拥有存档生命周期。** `civ_mcp.runtime` 不负责启动游戏、读档或持有
  FireTuner 生命周期；那些属于 host 侧 launcher（`civ_mcp.game_launcher` /
  `civ_mcp.game_lifecycle`）。
- **mutation 恰好发送一次。** 所有游戏动作经 `SessionKernel.execute`；发送后取消、
  连接中断、跨局回读都只留下 `UNKNOWN`。**UNKNOWN 永远不等于成功**，不得重发原操作
  来自动恢复。
- **未知不伪造成事实。** 读不到的游戏数据写 `unknown`（Game Memory 与 Observation
  都一样）；未遇到的文明不写进对手列表。
- 面向用户或模型的可读语义信息必须使用中文（说明、摘要、状态、阻塞原因、建议、
  验证结论）。协议字段名、工具名、参数名、错误码、ID、枚举值、文件路径、命令和
  原始游戏数据不翻译，可在其周围用中文解释。

## 游戏回合：入口与路由

`civ_agent` 的 LangGraph 工作流是唯一入口。每回合按 v7 §12 顺序：

```text
observe → retrieve_memory → jev_assess → deepseek_decide
  （可回边 search_rules / read_game_info）
  → jev_review → deepseek_finalize → execute → verify → append_game_memory
```

| 情形 | 读什么 |
|---|---|
| Runtime 当前能读什么、不能读什么 | [能力清单](docs/runtime-rebuild/capabilities.md) |
| 卡回合、自动存档、崩溃与恢复 | [游戏恢复](docs/agent-recovery.md) |
| 某条游戏机制的具体数值与规则 | [机制百科](docs/wiki/README.md)（先读 L0 决策速查） |
| v7 已冻结的设计决策与开放问题 | [v7 决策记录](docs/v7-decision-record.md) |

### 谁负责启动游戏与读档

Runtime 与 MCP 层都**不**启动游戏、**不**读档。host 侧负责这件事，已实测可用的
实现是：

- `civ_mcp.game_launcher`：启动 Civ VI、探测 FireTuner 端口。
- `civ_mcp.game_lifecycle.load_save_from_frontend(conn, save_name)`：在 Civ VI
  `MainMenu` Lua state 内用官方 `Network.LoadGame` + `Automation.SetAutoStartEnabled`
  完成读档与 Continue，**不用 OCR、不点击窗口**。
- `civ_mcp.game_lifecycle.verify_loaded_world(conn)`：读档后核验世界。

v7 基准存档的配置名是 `benchmark_start.Civ6Save`（由
`吉尔伽美什_turn_1.Civ6Save` 安装的 ASCII 副本）。存档名校验只允许
`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`，因此**不要**给读档入口传中文存档名。

### `scripts/launch_save.py`（OCR 菜单导航）

需要「先退出游戏 → 重新拉起并直接读到对局」时可用。它按 Steam → 主菜单 →
单人游戏 → 加载游戏 → 选档 → `CONTINUE GAME` 做 OCR 导航：

```bash
cd civ6-mcp
uv run python scripts/launch_save.py --kill-first              # 杀掉当前游戏并重新拉起，载入最近的自动存档
uv run python scripts/launch_save.py AutoSave_0221             # 指定存档（名字不带 .Civ6Save 后缀）
```

- 依赖：`uv sync --extra launcher-macos`（pyobjc 的 Quartz/Vision）。
- 权限：macOS 必须给**调用方**「屏幕录制」（`screencapture` + OCR）与「辅助功能」
  （`CGEvent` 点击、`osascript System Events`）。从 Terminal 运行通常已授权；从其它
  App（例如 DSH Desktop）代跑可能被系统拒绝，届时改由人工在 Terminal 执行。
- 脚本结束后不要用它去探 `4318`：FireTuner 只允许一个客户端。

## 开发：验证与提交

```bash
uv run pytest tests/ -q            # 全量离线测试（CI 同款）
uv run ruff check src tests scripts  # 静态检查（CI 同款）
uv run pytest tests/test_civ_agent_memory.py -q -k "memory_dir"  # 单文件 / 按关键字
```

- 按关键字筛选用真实存在的关键词：`-k "orphan"` 之类的写法会静默选中 0 个测试，
  看着像通过，实际上什么都没验证。筛选后确认 collected 数不为 0。
- 测试引用的第三方依赖必须在 `pyproject.toml` 中声明，否则全新克隆会收集失败并
  中断整个测试运行（`tests/test_test_dependencies.py` 会拦住）。
- 本地测试用一次性夹具、不触碰生产数据：跑测试、修由本次改动引起的失败、重跑受影响
  用例，都不必逐步征求同意。这是唯一授权的自主验证范围；真实游戏动作不在此列。
- 静态检查用 `ruff`，只启用能查出真实缺陷的窄规则集（配置在
  `pyproject.toml [tool.ruff.lint]`）。放宽规则要增量做。
- 交付前按改动范围自查本节的验证要求；新增/修改的机制细节同步更新对应文档，不要把
  设计说明留在提交信息里。
- 每次完成代码修改后必须创建一条 Git 提交，不要留下已验证但未提交的改动。提交前
  审查差异、只暂存本次任务的文件（不得捆绑用户已有或并发改动），信息用 Conventional
  Commits 格式的 `<type>(<scope>): <中文摘要>`
  （`feat`/`fix`/`refactor`/`test`/`docs`/`chore`）。默认只在当前分支提交，不推送、
  不改写历史，除非用户明确要求。

## 开发：设计不变量

动架构前先读 [Runtime Core](docs/runtime-rebuild/README.md)。以下是改代码时最容易
踩空、且测试会拦住的几条：

- **单一 mutation 入口**：所有游戏动作经 `SessionKernel.execute`（`civ_mcp/runtime/session.py`）。
  同一 `operation_id` 的并发调用只会实际发送一次；发送后取消或连接中断只写
  `UNKNOWN`，`OperationStore` 是 append-only 的事实账本。
- **`TurnLoop` 只有三种结局**：`ADVANCED`、`NEEDS_DECISION`、`RECOVERY_REQUIRED`。
  它不补发 end-turn，也不自动启动恢复；明确 `NOT_SENT` 的请求直接要求新决策。
- **`ContextBuilder` 前后各核验一次绑定**：它只组合 `CivAdapter` 的 typed read 与当前
  Session 事实，不能把切档后的事实与旧 branch operation 混进同一上下文。
- **telemetry 不能成为执行依据**：`civ_mcp.runtime.telemetry` 只追加本地 JSONL，不导入
  SessionKernel / CivAdapter / MCP surface / OperationStore；日志成功与否不得改变游戏
  执行事实。
- **`civ_agent` 的图状态按通道划分写入方**：`state.py` 里 `unknown` / `rule_queries` 是
  累加通道，其余是整块替换。节点不得手写游戏状态字段来「修正」Observation。
- **命名陷阱**：`execute_read` / `execute_write` 指 Lua 上下文（GameCore/InGame），不是
  读写语义；真正的读写区分在 `execute_mutation`。
- **`civ_mcp/server` 已不存在**。不要再引用它的 `pipeline` / 门禁分类 / 工具面数字；
  新的工具面是 `civ_mcp/runtime/server.py`（48 个 tool：23 只读 + 25 mutation）。
- **验证分层**：离线测试、FireTuner handshake、typed read 和真实回合动作是四个不同的
  证据层，不能用前者冒充后者。

其他文档：仓库文档总览 [docs/README.md](docs/README.md)，Runtime 能力清单
[capabilities.md](docs/runtime-rebuild/capabilities.md)，DSH Runtime overlay
[DSH 集成](integrations/deepseek-harness/README.md)，安装与基础用法 [README.md](README.md)，
历史变更 [CHANGELOG.md](CHANGELOG.md)。
