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

### D4 入口形态：独立命令行进程，完全绕开 DSH

- `civ_agent` 是**自己的 Python 主进程**，一条命令行启动，LangGraph 在进程内
  运行。
- **直接内调** `civ_mcp.runtime` 的 Python 对象（`CivAdapter` / `SessionKernel` /
  `OperationStore` / `TurnLoop`），**不经 MCP**。
- 不走 DSH：`integrations/deepseek-harness/` 与 `scripts/runtime_dsh` 不进入
  MVP 正式路径。
- **后果**：`civ_mcp.runtime.server` 的 48 个 MCP tool 在 MVP 主路径中不被调用，
  仅作为参考；`RuntimeMcpSurface` 同理。`@mcp.tool` 装饰器与 `FastMCP` 依赖
  在 MVP 中不再需要，删除时机留到 M01 之后单独确认。

### D5 DeepSeek → 动作：原生 function calling

- 把 Runtime 的 **25 个 mutation** 包装成 LangChain tools 绑定给
  `ChatDeepSeek`，在 `deepseek_decide` 阶段由模型自主调用，从而拿到真实可行性
  数据（如 `get_unit_attack_target`）。不采用"模型输出 JSON、节点层解析"的
  方案。
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
- **配套要求**：必须有墙钟超时与总回合数停止条件兜底，见 O6 / O7。

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

## 3. 仍未决定的事项

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

### O2 launcher 装配方式

`civ_agent` 需要 `assemble_runtime(adapter, store, branch_token=...)` + a
`RuntimeConnection` + a store 路径。launcher 需要自己完成这套装配，并选一个
稳定的 branch token（例如基准存档每次运行生成一个新的）。

### O3 `@mcp.tool` / `FastMCP` 去留

D4 之后 MCP 不进主路径。建议保留 `civ_mcp/runtime/server.py` 作为参考适配层
（它已冻结并被 `tests/test_runtime_entrypoint.py` 等覆盖），但**删除**
`civ_mcp/server/`（旧 112 工具 MCP 面）。两者的名字相近但完全不同，见 §5。

### O4 总运行成本与停止条件

D7 不设循环上限，加上"整局几百回合"，需要明确：
- 总回合上限或总墙钟上限；
- 单回合墙钟超时；
- 是否需要在超时后把该回合标记为"未完成"并写入 Memory。

### O5 脚本命名冲突 —— 已定位受影响测试

`scripts/civ6_agent` 已存在，是旧 DSH 受控回合循环（含
`--play-profile legacy|lean`）。D4 之后它属于 §13.1 的 "legacy / lean play
profile"，需要删除或改名，新 launcher 需要新名字。

实测：删除它会连带使 `tests/test_civ6_agent_entrypoint.py` 的 5 项测试失败
（其中 `test_default_profile_is_legacy` 正断言默认 `legacy`）。

### O6 `uv.lock` 重算

加入 `langgraph` / `langchain-typesafe`（prerelease）/ `langchain-deepseek`，
以及 D10 改名后会触发大范围 lock 变更。建议与 D10 合并成一次重算。

### O7 旧测试的去留 —— 已精确测量

见 §4，结论是 **63 个测试文件**硬依赖旧认知栈。

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
| 前置 | 拍定 O2 / O3 / O4 / O5 / O6 | — | 否 |
| M01 | 删旧认知系统（边界见 §4，已实测） | — | 否 |
| M02 | 建 `src/civ_agent/` 包骨架 | — | 已完成 |
| M03 | 装 `langgraph` / `langchain-typesafe`(a) / `langchain-deepseek`；fail-fast | O6 | 是 |
| M04 | Jev 节点（`jev_assess` / `jev_review`） | M03 | 是 |
| M05 | DeepSeek 节点 + 25 mutation 工具绑定（D5） | M03 | 是 |
| M06 | Observation 组合（`civ_mcp.runtime.context`） | — | 已完成 |
| M07 | Game Memory Writer | — | 已完成 |
| M08 | Memory Search（全文） | M07 | 已完成 |
| M09 | Rule Search | — | 否 |
| M10 | LangGraph 只读跑通 | M02 M04 M05 M06 | 是 |
| M11 | `read_game_info` / `search_rules` 回边 | M10 | 是 |
| M12 | 接 Runtime mutation | M11 | 是 |
| M13 | 完整 Turn Memory 追加 | M12 M07 | 是 |
| M14 | End Turn / AI Turn | M12 | 是 |
| M15a | 冒烟：固定回合上限整局 | M14 M13 D3 | 是 |
| M15b | 全量：Turn 1 → Game Over | M15a | 是 |
| 横切 | launcher（启动 + 读档 + D1 安装） | D2 O1(已定) O2 O5 | 是 |
| 横切 | `civ6-agent` 改名（D10） | — | 否 |
