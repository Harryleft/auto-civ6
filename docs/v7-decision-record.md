# v7 方案实施决策记录

本文件冻结 `Civ6_Jev_DeepSeek_LangGraph_MVP_重构方案_v7_实施版.md` 在实施前
必须澄清、且方案原文未写定的事项。方案原文与本文件冲突时，以本文件为准。

基线仓库：`civ6-mcp`（`main`）。当前 HEAD：`e51173a`。

---

## 1. 已冻结决策

### D1 基准存档：安装 ASCII 副本

- 配置项定为 `benchmark_start.Civ6Save`。
- 源存档为 `~/Library/Application Support/Sid Meier's Civilization VI/
  Sid Meier's Civilization VI/Saves/Single/吉尔伽美什_turn_1.Civ6Save`。
- launcher 启动前把它安装/复制为存档目录下的 `benchmark_start.Civ6Save`。
- **理由**：`civ_mcp/game_lifecycle.py:20` 的
  `_SAVE_NAME = ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$` 会让
  `load_save_from_frontend()` 直接拒绝 `吉尔伽美什_turn_1`。该正则用于防止
  存档名注入 Lua，放宽到 Unicode 需要额外的转义论证；安装 ASCII 副本不触碰
  安全边界。
- **落点**：新增一个 install-benchmark 脚本；仓库内提供
  `fixtures/benchmark/` 说明与校验。

### D2 读档：复用现有 FrontEnd API 路径，不写新的

已存在且已验证的能力，直接复用，**不重新实现**：

- `civ_mcp.game_launcher`：启动游戏、探测 FireTuner、`_is_tuner_port_open()`。
- `civ_mcp.game_lifecycle.load_save_from_frontend(conn, save_name)`：在 Civ VI
  `MainMenu` Lua state 内用官方 `Network.LoadGame` +
  `Automation.SetAutoStartEnabled(true)` 完成读档与 Continue，**不用 OCR、不点击
  窗口**。
- `civ_mcp.game_lifecycle.verify_loaded_world(conn)`：读档后核验世界。
- `civ_mcp.server.assembly._auto_resume`：完整的"未运行则启动 → 等待 MainMenu
  state → FrontEnd 载入"编排，可作为 launcher 的实现参考。

### D3 Game Over：给 Runtime 新增 typed 读取

- 在 `CivAdapter` 新增 game over / 胜负判定的 typed read，使 M15 能把
  `最终结果` 与 `结束回合` 写入 Game Memory。
- **不复用** `civ_mcp/game_over_watchdog.py`：它绑定即将删除的旧 `GameState`。
- 参考现有 `CivAdapter.read_victory_progress()`（`lua/victory.py`）的 shape。

### D4 入口形态：独立命令行进程 + MCP 客户端（由 O2 修订）

- `civ_agent` 是**自己的 Python 主进程**，一条命令行启动，LangGraph 在进程内
  运行。
- 它**经 MCP 客户端**（stdio）连接 `civ_mcp.runtime.server` 来读写游戏，**不**在
  进程内直连 `CivAdapter` / `SessionKernel`。branch token 与 store 路径经
  `CIV_MCP_RUNTIME_BRANCH` / `CIV_MCP_RUNTIME_STORE` 传给 server 子进程；
  每次从基准存档起跑生成新 branch，换局必须重启 server 子进程。（细节见 §3 O2）
- **不**走 DSH 宿主：不使用 `integrations/deepseek-harness/` 的 overlay，也不使用
  `scripts/runtime_dsh` 启动 DSH。`civ_agent` 自己拉起 Runtime server 子进程。
- 修订原因：MCP 工具面需要保留，好让 CLI 能直接操作游戏，同时让 48 个工具对
  DeepSeek 可用。走 MCP 使工具只有一份实现，避免两套入口同时竞争 FireTuner。

### D5 DeepSeek → 动作：原生 function calling，工具由 MCP 动态发现

- 把 Runtime 的工具绑定给 `ChatDeepSeek`，在 `deepseek_decide` 阶段由模型自主
  调用，从而拿到真实可行性数据（如 `get_unit_attack_target`）。不采用"模型输出
  JSON、节点层解析"的方案。
- **工具来源由 O2 修订**：不再手写 25 个 mutation 的包装层，而是从 MCP server
  的 `list_tools` 结果动态发现并逐一包装成 LangChain tools。这样工具清单始终与
  Runtime 实际暴露的能力一致，不会漂移。
- 25 个 mutation 清单（来自 `src/civ_mcp/runtime/server.py`）：
  `save_handoff` `move_unit` `attack_unit` `attack_city` `build_improvement`
  `propose_trade` `upgrade_unit` `promote_unit` `send_envoy` `appoint_governor`
  `assign_governor` `promote_governor` `change_government` `set_policies`
  `set_city_production` `purchase_item` `make_trade_route` `set_research`
  `set_civic` `choose_pantheon` `choose_dedication` `recruit_great_person`
  `found_city` `end_turn` `resume_turn_decision`
- 23 个只读 tool 作为配套读取能力一并暴露。

### D6 DeepSeek 端点：官方 API

- `api.deepseek.com`，只设 `DEEPSEEK_API_KEY`，不配 `base_url`。
- 使用 `langchain_deepseek.ChatDeepSeek` 默认行为。

### D7 每回合循环不做上限

- `deepseek_decide` 回边 `search_rules` / `read_game_info` 的次数**不设硬上限**，
  信任模型自己收敛。
- **配套兜底**：单回合墙钟 3 分钟 + 总 50 回合（见 §3 O4）。

### D8 M01 删除边界：只删代码 + 只测它的测试

- 只删 `src/civ6_belief_engine/`，以及**仅**为它存在、脱离它无法运行的测试。
- 本轮**不删**：`web/`、`docs/` 里的 belief/governance 文档、`evals/`、
  `experiments/`。这些留到后续任务卡单独确认。
- 验收口径不变：仓库正式运行路径不再 import `civ6_belief_engine`，新 Runtime
  可单独 import / test 通过。

### D9 整局验收：先冒烟，后全量

- M15 分两步。第一步用**固定回合上限**（初值待定，建议 50 或 100）跑通并验证：
  每回合持续追加、一局只有一个 Markdown、duplicate mutation = 0、
  false confirmed = 0。
- 冒烟通过后再做"Turn 1 → Game Over"的全量验证。

### D10 项目改名

- `pyproject.toml` 的 `project.name` 由 `civ6-belief-engine` 改为 `civ6-agent`。
- **保留** `civ_mcp` 包名，MCP 命令名不变。

---

## 2. 本轮发现、需要修正或补充的方案问题

| # | 方案原文 | 实际情况 | 处置 |
|---|---|---|---|
| P1 | §2 用 `benchmark_start.Civ6Save` | 你指定 `吉尔伽美什_turn_1`，但它过不了存档名校验 | 见 D1，安装 ASCII 副本 |
| P2 | §2 基准存档需"人工准备" | 已有 `game_lifecycle` 的官方 FrontEnd 读档路径 | 见 D2，不要重写 |
| P3 | §10 要写"最终结果" | 新 Runtime 无 game over 读取 | 见 D3 |
| P4 | §14 目录只列 `civ_mcp/runtime` + `civ_mcp/civ` | 读档/启动所需模块（`game_lifecycle` `game_launcher` `connection`）不在"保留"清单里 | 见 O1 |
| P5 | §2 未明"谁负责读档" | Runtime 明确声明不拥有存档生命周期 | 见 D2 + launcher |
| P6 | §12 未说明 DeepSeek 如何产生可执行动作 | Runtime 有 25 个 mutation | 见 D5 |
| P7 | `langchain-typesafe` 是 alpha | PyPI 现为 `0.0.1a2` 预发布 | `uv add` 需允许 prerelease；API 可能变动，需锁版本 |
| P8 | §13.2 "保留 Runtime operation store / TurnLoop" | `runtime/__init__.py` 无导出，`server.py` 是唯一装配消费方 | launcher 需直接 `assemble_runtime()`，见 O2 |

---

## 3. 已决定的实施细节（原开放问题）

以下 O1–O7 全部已定案，实施时不再需要另行确认。

### O1 读档/启动模块的归属 —— 已测量，建议保留

launcher 需要 `game_lifecycle` / `game_launcher`，但方案 §13.2 的"保留"清单里
没有它们，§14 的新目录也没给位置。已实测其依赖闭包：

```text
game_lifecycle → civ_mcp.lua, civ_mcp.connection
connection     → civ_mcp.tuner_client, civ_mcp.lua._helpers
game_launcher  → （无内部依赖）
logger         → civ_mcp.telemetry → civ_mcp.run_id
（telemetry / run_id 只依赖标准库）
```

**结论**：该闭包完全不含 belief engine，也不含旧 server。保留即可，无需归入
`civ_mcp.runtime`，从而维持"Runtime 不拥有存档生命周期"这条边界。
`run_id` / `telemetry` 必须随 `logger` 一起保留。

### O2 接入形态：经 CLI MCP 客户端调 Runtime —— 已定

`civ_agent` **不**在进程内直连 `CivAdapter`/`SessionKernel`，而是作为 **MCP 客户端**
通过 stdio 连接 `civ_mcp.runtime.server`，把它的 48 个工具作为动作库使用。

- 唯一的装配方是 `civ_mcp.runtime.server` 的 lifespan：它自己
  `RuntimeConnection.connect()` → `assemble_runtime(...)` → 关闭时 `store.close()`。
  `civ_agent` 不重复这套装配。
- branch token 经启动 server 子进程时的环境变量传入：
  `CIV_MCP_RUNTIME_BRANCH`（`civ_mcp/runtime/server.py:44` 的 `RUNTIME_BRANCH_ENV`），
  store 路径经 `CIV_MCP_RUNTIME_STORE`。**每次从基准存档起跑生成一个新的 branch
  token，从不复用**，避免跨局回读把不同 run 的 operation 串在一起。
- 因为 branch 只在 lifespan 绑定时读取一次，**换局必须重启 server 子进程**，
  不能在同一个 server 进程里切 branch。
- 直接收益（取代原 D5 的手写绑定）：48 个工具可经 MCP `list_tools` 动态发现，
  再逐一包装成 LangChain tools 喂给 `ChatDeepSeek`，因此
  **不需要为 25 个 mutation 手写包装层**，且 CLI 手动操作与 `civ_agent` 共用同一份
  工具实现，不存在两套竞争 FireTuner 的实现。
- 参考实现：`scripts/_mcp_call.py`（单次调用）与 `scripts/_mcp_session.py`
  （stdin 逐行 JSON 的常驻会话）。

### O3 MCP 工具面：保留并进入主路径 —— 已定

`civ_mcp/runtime/server.py`（48 个 tool：23 只读 + 25 mutation）**保留**，且不再是
"仅供参考"：按 O2 它是 `civ_agent` 的实际动作库入口。因此

- **不**拆分纯函数层，`@mcp.tool` 装饰器保留；
- `civ_mcp/server/`（旧 112 工具面）已在 M01 删除，与本文档无关，见 §5 P9；
- `scripts/_mcp_call.py` / `_mcp_session.py` 从"旧 pi 临时脚本"升级为正式的
  CLI 操作入口，M01 未删除它们。

### O4 停止条件：单回合 3 分钟 / 总 50 回合 —— 已定

`deepseek_decide` 的循环次数仍然不设上限（D7），由墙钟兜底：

- **单回合墙钟超时 3 分钟**；
- **总回合上限 50 回合**（冒烟用，见 D9）；
- 超时或触顶时，该回合标记为**未完成**并写入 Game Memory，**不伪造成
  CONFIRMED**；本局以"未结束"收尾，不调用 `finish_game` 写胜负。

### O5 新命令行入口：`scripts/civ6_run` —— 已定

`scripts/civ6_agent` 已在 M01 删除（属于 §13.1 的 legacy/lean play profile）。
新入口命名为 **`scripts/civ6_run`**，同时提供 `python -m civ_agent` 等价入口。

### O6 依赖：允许 prerelease 并锁死精确版本 —— 已定

加入 `langgraph`、`langchain-deepseek`、`langchain-typesafe`。其中
`langchain-typesafe` 目前是 alpha 预发布版（`0.0.1a2`）：

- `uv add` 需显式允许 prerelease；
- 必须**锁死精确版本**（`==0.0.1a2`），因为 alpha 的 API 可能随时变动；
- 升级该依赖时视为一次独立改动，需重跑 Jev 节点的测试。

### O7 旧测试的去留 —— 已精确测量

见 §4，结论是 **74 个测试文件**随 M01 一起删除（实测，非估算）。


---

## 4. M01 精确删除边界（O7 实测结果）

在 `civ6_belief_engine` 临时不可导入、并把根 `conftest.py` 换成"引擎缺失时
夹具 skip"的实验版本后，跑全量测试得到：

```text
63 collection errors（硬依赖：模块级 import 旧认知栈）
10 failed（软依赖：与旧 play profile / server 面绑定）
605 passed
```

实验后已还原 `civ6_belief_engine`（39 个 .py 文件 shasum 逐一比对一致）、还原
根 `conftest.py`，并重跑全量确认恢复到 `1434 passed`。

### 4.1 production 侧必须删除的模块（10 个，实测 import 旧引擎）

```text
src/civ_mcp/belief_mode.py
src/civ_mcp/facts.py
src/civ_mcp/game_state.py
src/civ_mcp/server/assembly.py
src/civ_mcp/server/governance_snapshot.py
src/civ_mcp/server/pipeline.py
src/civ_mcp/server/tools/actions.py
src/civ_mcp/server/tools/belief_tools.py
src/civ_mcp/server/tools/governance_adapters.py
src/civ_mcp/server/tools/world_model.py
```

`civ_mcp/runtime/` 与 `civ_mcp/civ/` 实测**零**belief-engine 引用。

### 4.2 硬依赖测试（63 个，随旧栈一起删除）

```text
test_audit_fixes              test_belief_mode_server        test_facts
test_authorization_integrity  test_belief_properties         test_forecast_bayes
test_belief_bind_offload      test_belief_tool_pipeline      test_forecast_calibration
test_belief_coverage          test_canonical_hash            test_forecast_extrapolation
test_belief_engine            test_climate_overview          test_gate_fixes
test_belief_engine_p0         test_collection_snapshot       test_governance_core
test_belief_journal_isolation test_dashboard_state           test_governance_dedup
test_belief_mode              test_department_civics         test_governance_entity_types
test_department_coordinator   test_department_diplomacy      test_governance_inputs
test_department_economy       test_department_great_people   test_governance_server
test_department_military      test_department_production     test_governance_snapshot
test_department_review_guard  test_department_science        test_derivation
test_derived_read_caches      test_dsh_auto_resume           test_end_turn_budget
test_era_progress             test_graph_context             test_graph_governance
test_graph_model              test_graph_replay_verification test_harness_belief_flow
test_history_windows          test_journal_single_writer     test_lean_profile
test_load_cache_lifecycle     test_notification_arbitration  test_phase2_threat_chain
test_presentation             test_read_cache                test_religion_overview
test_reload_epoch_recording   test_result_filter             test_runtime_recovery_contract
test_server_shutdown          test_shared_validation         test_tool_gate_coverage
test_tool_surface             test_turn_context              test_world_model_engine
```

### 4.3 软依赖测试（10 项失败，O5 解决后单独处理）

```text
tests/test_civ6_agent_entrypoint.py   （5 项：断言 legacy 默认 profile）
tests/test_experiment_baseline.py     （2 项：旧实验基线脚本）
tests/test_product_package_boundary.py（1 项：断言旧 server 工具边界）
tests/test_turn_in_progress.py        （2 项：旧 reload epoch 语义）
```

### 4.4 存活的测试文件（117 - 63 = 54 个）

`test_civ_adapter.py`、`test_runtime_*.py`（22 个）、`test_civ6_agent_*`、
`test_civ_agent_memory.py` 等。根 `conftest.py` 现有两个夹具全部服务于旧引擎，
M01 后可整体删除；存活测试不依赖它。

---

## 5. 新增发现（实施 M02 阶段）

### P9 `civ_mcp/server/` 与 `civ_mcp/runtime/server.py` 是两套东西

- `civ_mcp/server/`：旧 MCP 面，112 个工具，经 `pipeline._logged` 与
  belief/治理门禁（AGENTS.md 的硬规则描述的就是它）。**属于删除范围**。
- `civ_mcp/runtime/server.py`：新 Runtime Core 的 MCP 面，48 个 tool
  （23 只读 + 25 mutation）。D4 后不进主路径，但函数体就是 `civ_agent` 的
  动作库来源，**保留**。

AGENTS.md 中"不要在 `civ_mcp/server/` 包外新增游戏动作"等规则，M01 之后其
约束对象消失，需要同步更新该文件的生命周期说明。

### P10 `pyproject.toml [tool.mutmut]` 指向将被删除的文件

```toml
source_paths = ["src/civ6_belief_engine/belief_engine.py", ...]
```

M01 后需一并删除该节，否则 mutation 配置悬空。


---

## 6. 任务卡执行顺序

| 卡 | 内容 | 依赖 | 阻塞 |
|---|---|---|---|
| 前置 | O1–O7 全部定案（见 §3） | — | 已完成 |
| M01 | 删旧认知系统（边界见 §4） | — | 已完成 |
| M02 | 建 `src/civ_agent/` 包骨架 | — | 已完成 |
| M03 | 装 `langgraph` / `langchain-typesafe==0.0.1a2` / `langchain-deepseek`；fail-fast | O6 | 否 |
| M04 | Jev 节点（`jev_assess` / `jev_review`） | M03 | 是 |
| M05 | DeepSeek 节点 + MCP 动态发现的工具绑定（D5） | M03 | 是 |
| M06 | Observation 组合（经 MCP 的 `get_runtime_context`） | O2 | 已完成（内调版），O2 后需改为 MCP 来源 |
| M07 | Game Memory Writer | — | 已完成 |
| M08 | Memory Search（全文） | M07 | 已完成 |
| M09 | Rule Search | — | 否 |
| M10 | LangGraph 只读跑通 | M02 M04 M05 M06 | 是 |
| M11 | `read_game_info` / `search_rules` 回边 | M10 | 是 |
| M12 | 接 Runtime mutation | M11 | 是 |
| M13 | 完整 Turn Memory 追加 | M12 M07 | 是 |
| M14 | End Turn / AI Turn | M12 | 是 |
| M15a | 冒烟：单回合 3 分钟 / 总 50 回合 | M14 M13 D3 | 是 |
| M15b | 全量：Turn 1 → Game Over | M15a | 是 |
| 横切 | `scripts/civ6_run`（启动 + 读档 + D1 安装 + 拉起 Runtime server） | D1 D2 O2 O5 | 是 |
| 横切 | MCP 客户端层（stdio 连 Runtime server，动态发现工具） | O2 | 是 |

