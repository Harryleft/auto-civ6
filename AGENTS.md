# civ6-belief-engine — 仓库入口

这是通过 FireTuner 连接正在运行的《文明 VI》的 MCP 服务（产品边界见 [产品架构](docs/product-architecture.md)）。
仓库有两条互不相同的任务线——**游戏回合**和**开发改动**——先判断当前属于哪一条，再读对应文档。
本文只放硬规则、完成定义和文档路由；细节一律在被路由的文档里，不要凭本文或旧会话推断机制。

## 完成定义（先定终点，再开工）

- **开发改动**：完成的标志是**改动已落地、验证通过、已提交**，不是「第一版写完」。除非用户明确要求先停下来评审，否则一路做到上述状态再汇报；中途不要为「请确认下一步」而停。
- **游戏回合**：完成的标志是**本回合决策已处理或显式记录推迟**，并且已调用 `skip_remaining_units` → `end_turn`。不要停在「给出建议」或「报告发现」上；没有阻塞就继续把本回合走完。
- 两种情况都要在回复末尾报告证据：改了什么、跑了什么验证、结果如何；做了什么、结果如何、哪些事项被推迟及原因。

## 硬边界

- FireTuner 只允许一个客户端。不要并行运行 Pi、Codex、独立 `civ-mcp`、连接测试或第二个 DSH MCP 客户端。
- DSH 不负责启动文明 VI：必须先进入一局游戏再启动 DSH。Civ 6 进程、DSH 页面、Python 进程或 4318 监听单独存在，都不等于集成可用。
- 不要单独运行 `uv run civ-mcp`；DSH wrapper 负责拉起唯一的 MCP 进程。
- `CIV_MCP_BELIEF_MODE` 与 `get_game_overview` 返回的 `RUNTIME POLICY` 是运行模式的唯一来源。返回 `BELIEF_GATE_REQUIRED` 时，按返回的精确 action intent 完成路由后只重试一次；`bypassed` 模式不要调用路由工具。
- 原始工具结果归 DSH transcript/telemetry；信念引擎只保存规范化事实、指纹和决策/动作关联。
- 面向用户、DSH 模型或 MCP 工具调用方的可读语义信息必须使用中文（说明、摘要、状态、阻塞原因、建议、验证结论）。协议字段名、工具名、参数名、错误码、ID、枚举值、文件路径、命令和原始游戏数据不翻译，可在其周围用中文解释。

## 游戏回合：入口与路由

进入游戏后，每回合第一条游戏请求必须是 `mcp__civ6__get_game_overview`；读取成功前不要执行游戏动作或 `end_turn`。之后按需读取，不要全文搬进会话：

| 情形 | 读什么 |
|---|---|
| 启动、验证 FireTuner 与 DSH、判断集成是否真的可用 | [启动与验收](docs/agent-startup.md) |
| 每回合的查询顺序、信念/治理门禁、Diary 字段、10/20/30 回合检查 | [回合规则](docs/agent-turn-loop.md) |
| 结束回合前的**主清单**：逐领域核对安全/科技/市政/经济/军事/城市/外交/文化/宗教 | [CHECKLIST.md](CHECKLIST.md) |
| 具体工具、单位动作、阻塞项、生产、区域、商路、世界议会 | [工具与动作](docs/agent-tools.md) |
| 神级生存、扩张、外交、战争、胜利路线 | [策略](docs/agent-strategy.md) |
| 卡回合、自动存档、崩溃与恢复 | [游戏恢复](docs/agent-recovery.md) |
| 某条游戏机制的具体数值与规则 | [机制百科](docs/wiki/README.md)（先读 L0 决策速查） |
| 信念事件、派生规则、覆盖审计 | [信念引擎](docs/belief-engine.md) |
| 提案、批评、议会、预算锁、ActionIntent | [治理系统](docs/governance-system.md) |

结束回合前必须逐项核对主清单：任何一项为「否」= 该领域存在未完成决策，先处理，或显式记录推迟原因（写入 Diary.planning），不得假装已处理；冲突时按更严格者执行并报告。回复末尾报告 `回合清单核对：通过 x/16，N/A y`。

### 退出游戏后直接进对局：`scripts/launch_save.py`

需要「先退出游戏 → 重新拉起并直接读到对局」时用这个脚本。它按 Steam → 主菜单 → 单人游戏 → 加载游戏 → 选档 → `CONTINUE GAME` 的顺序做 OCR 导航，不需要人手点菜单：

```bash
cd civ6-mcp
uv run python scripts/launch_save.py --kill-first              # 杀掉当前游戏并重新拉起，载入最近的自动存档
uv run python scripts/launch_save.py AutoSave_0221             # 指定存档（名字不带 .Civ6Save 后缀）
uv run python scripts/launch_save.py --no-launch AutoSave_0221 # 游戏已在运行，只做菜单导航
```

- 依赖：`uv pip install 'civ6-belief-engine[launcher-macos]'`（pyobjc 的 Quartz/Vision）；本仓库 `.venv` 已具备。
- 权限：macOS 必须给**调用方**「屏幕录制」（`screencapture` + OCR）与「辅助功能」（`CGEvent` 点击、`osascript System Events`）。从 Terminal 运行通常已授权；从其它 App（例如 DSH Desktop）代跑可能被系统拒绝，届时改由人工在 Terminal 执行。
- 存档位置：脚本默认在 `Saves/Single/auto/AutoSave_*.Civ6Save` 里找，并会点进「Autosaves」页；`0_MCP_*.Civ6Save` 位于 `Saves/Single/`（普通存档列表），所以**优先传 `AutoSave_*`**。要回到 MCP 存档请用受控恢复：`CIV_MCP_DSH_AUTO_RESUME=1 ./scripts/civ6_launch web|agent`（经 Civ VI FrontEnd API 直接载入最新 `0_MCP_*.Civ6Save`，不点菜单）。
- 脚本结束后不要用它去探 `4318`：FireTuner 只允许一个客户端，连接与工具调用一律交给 DSH/MCP。

## 开发：验证与提交

```bash
uv run pytest tests/ -q            # 全量离线测试（CI 同款）
uv run ruff check src tests        # 静态检查（CI 同款）
uv run pytest tests/test_belief_engine.py -q -k "orphan"   # 单文件 / 按关键字
./scripts/deepseek_harness check   # 安装或配置变更后的环境检查
./scripts/belief_coverage.py       # 信念覆盖审计：决策支持率 / 预测结算
```

- 测试隔离与账本是硬约束：测试不得写入 `~/.civ6-mcp/beliefs/`（autouse 夹具已重定向到 `tmp_path`，新增 `BeliefEngine` 仍要显式传 `directory=`）；测试引用的第三方依赖必须在 `pyproject.toml` 中声明。
- 本地测试用一次性夹具、不触碰生产数据：跑测试、修由本次改动引起的失败、重跑受影响用例，都不必逐步征求同意。这是唯一授权的自主验证范围；真实游戏动作不在此列。
- 静态检查用 `ruff`，只启用能查出真实缺陷的窄规则集（配置在 `pyproject.toml [tool.ruff.lint]`）。放宽规则要增量做，不要一次性打开数百项风格规则。
- 交付前按改动范围自查本节的验证要求；新增/修改的机制细节同步更新对应文档，不要把设计说明留在提交信息里。
- 每次完成代码修改后必须创建一条 Git 提交，不要留下已验证但未提交的改动。提交前审查差异、只暂存本次任务的文件（不得捆绑用户已有或并发改动），信息用 Conventional Commits 格式的 `<type>(<scope>): <中文摘要>`（`feat`/`fix`/`refactor`/`test`/`docs`/`chore`）。默认只在当前分支提交，不推送、不改写历史，除非用户明确要求。

## 开发：设计不变量

动架构前先读 [架构](docs/architecture-diagrams.md)、[信念引擎](docs/belief-engine.md)、[治理系统](docs/governance-system.md)；测试分类与夹具约定见 [测试体系](docs/testing.md)。以下是改代码时最容易踩空、且测试会拦住的几条：

- **单一写入通道**：游戏工具必须经 `server/pipeline.py` 的 `_logged` 管道，信念/治理控制面工具经 `_belief_tool`，不要绕开；少数进程级工具（`kill_game` / `launch_game` / `restart_and_load` / `get_diary` / `get_governance_brief`）目前两条都不经，改动它们时优先接入。新增游戏动作必须走 `execute_mutation`（恰好发送一次、失败即结果未知，语义见 [游戏恢复](docs/agent-recovery.md)），并配离线回归测试；重命令显式传 `timeout=SLOW_MUTATION_TIMEOUT`。
- **门禁分类必须显式**：新增 MCP 工具必须归类到 `pipeline._BELIEF_GATED_TOOLS`、`_ROUTINE_TOOLS` 或 `_CONDITIONAL_GATE_TOOLS` 之一；未归类 fail-closed，`tests/test_tool_gate_coverage.py` 会失败。`src/civ_mcp/server/` 包内 112 个工具（`tests/test_tool_surface.py` 会核对本文这个数字）。
- **命名陷阱**：`execute_read` / `execute_write` 指 Lua 上下文（GameCore/InGame），不是读写语义；真正的读写区分在 `execute_mutation`。
- **事件溯源不可绕过**：账本 append-only，同一账本同时只允许一个进程写入，实体用墓碑（`deleted`/`archived`）不物理删除，新代码不得直接改内存态；所有读档路径都必须调用 `pipeline._record_game_reload_epoch`。未完成授权会让回合门禁 fail-closed（细节见 [治理系统](docs/governance-system.md)）。
- **授权契约不可改写**：路由时要求提案当前内容与 `council_decision.approved_intents` 相等，`engine.update` 拒绝改写 decision 的 `action_intent` / `council_decision_id` / `args_hash`；改动路由或提案结构时同步看 `tests/test_authorization_integrity.py`。
- **图重放默认只校验最新 checkpoint**（`verify="final"`）：逐 delta 校验会退化成 O(D×V)，定位分歧点时才用 `verify="all"`。
- **结果过滤只作用于模型面副本**：`civ_mcp/result_filter.py` 目前只覆盖 `get_governance_brief` / `get_belief_state` / `get_belief_trace` 三个控制面工具，其余工具没有体积上限；阈值由 `CIV_MCP_RESULT_*` 控制，遥测保留原始全文。
- **验证分层**：离线测试、FireTuner handshake、MCP 工具读取和真实回合动作是四个不同的证据层，不能用前者冒充后者。

其他文档：仓库文档总览 [docs/README.md](docs/README.md)，Diary / 工具日志 / 空间注意力数据流 [可观测性](docs/observability.md)，图工程当前方案与验收门禁 [graph_plan/README.md](graph_plan/README.md)，DSH overlay 配置与安全决策 [DSH 集成](integrations/deepseek-harness/README.md)，安装与基础用法 [README.md](README.md)，历史变更 [CHANGELOG.md](CHANGELOG.md)。
