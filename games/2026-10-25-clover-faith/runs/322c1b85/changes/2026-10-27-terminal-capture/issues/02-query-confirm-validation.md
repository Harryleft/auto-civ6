# 02: 验证实体查询零重发确认并归档终局边界

**What to build:** 终局证据稍后到达时，通过现有命名 CLI 的实体查询确认旧请求并准确展示结果，核实无任何新攻击；为修复版测试、原生胜利现场和旧 owner 未加载补丁分别给出可审计结论。

**Blocked by:** 01: 以精确终局证据确认最后一次近战占领。

**Status:** resolved

- [x] 隔离场景中先建立未决请求，再到达终局证据，通过单位与城市查询完成同 owner 的只读确认，不再调用攻击入口。
- [x] 覆盖查询前后 pending、原生操作计数为一、确认结果与 CLI 的既有展示行为；无证据时查询不清门禁。
- [x] 不新建通用确认或清理入口；仅对已实现的公开行为补必要协议或展示修复。
- [x] 定向测试与两轴审查通过；规格、任务状态、提交和验收证据相互关联。
- [x] 原终局、原存档、原日志与 owner 保留；现场只读复核由主代理执行，旧进程未加载补丁的限制明确记录，不冒充实机修复通过。

## Comments

本任务不需要再次输入攻击命令，也不需要为加载新代码退出原 CLI。现有旧 owner 的实机核对由主代理集中完成，实施子代理不得访问真实游戏。

### 实施结果（待主代理合并、两轴审查和闭票）

- 分支 `codex/terminal-capture-02`，提交 `73a1e72`；已正常合并集成分支最新 tip，工作树干净，未 push。
- 新增 4 项命名 CLI 纵向回归：真实 `DebugSession` 的 `unit list`／`city list` → 真实 server 工具 → `GameState` → 生成的 Lua；仅用 in-process MCP 传输及隔离原生 API 边界，不替换查询或确认器。
- 同一请求先返回未知，随后只通过实体查询和 `pending` 检查完成；查询阶段不再调用攻击入口，原生操作总数恒为 1，session/PID 始终一致。无终局证据的两个反例保持未决。实体正文保留攻击者的实际相邻位置。
- 将相同用例导向临时源副本中的基点 `ca802af` 攻城模块，两个正例精确红在 `[True, True] != [True, False]`，两个负例通过；修复版四例通过。未改动工作树中的已合入实现以制造红灯。
- 更新一条基点已有的旧文案断言：已撤销检查点的恢复仍必须拒绝，按当前公开错误码 `UNREGISTERED_CHECKPOINT` 断言，保留 save pending 和原生保存次数约束；无生产行为修改。
- 更新 CLI 和架构说明，区分 `completion:terminal_conquest` 与实际进城，说明实体查询确认、纯内存 pending 和旧 owner 不能热加载的边界。
- 8 文件定向回归：`381 passed in 35.82s`，无排除项；编译与 `git diff --check` 通过。两轴审查和现场复核由主代理完成，未在本票冒称已完成。
- 证据：`/private/tmp/civ6-terminal-capture-02-query-red.log`、`...query-green.log`、`...old-assert-red.log`、`...targeted-final.log`。红灯源副本仅供隔离测试，路径记录在 `/private/tmp/civ6-terminal-capture-02-baseline-path`。
- 测试命令：`PYTHONPATH=<任务树>/src:<任务树>/tests UV_PROJECT_ENVIRONMENT=<主仓库>/.venv UV_CACHE_DIR=/private/tmp/civ6-terminal-capture-uv uv run --locked --no-sync pytest -q -o cache_dir=/private/tmp/civ6-terminal-capture-02-pytest tests/test_unit_attack_city.py tests/test_attack_mode_lua.py tests/test_action_confirmation.py tests/test_pending_dispatch.py tests/test_terminal_boundary_lua.py tests/test_terminal_capture_queries.py tests/test_frequent_commands.py tests/test_debug_cli.py`。
- 未访问真实游戏、CLI PTY、FireTuner、原存档或原终局证据；未创建新生产入口或确认状态副本，旧 owner 与 pending 未由本票更改。

### 双轴审查修复（P2 回执展示，待合入复核）

- 独立树 `terminal-capture-review-fix` 基于集成 tip `73a1e72`，分支 `codex/terminal-capture-review-fix`，提交 `cc84abf5a4ff56e8fcf042e32d24effb738b2fee`；正常合并最新集成分支时为 `Already up to date.`，未改旧任务02树，未 push。
- 先在真实命名 CLI 纵向正例断言结果含 `completion:terminal_conquest`，复现 `2 failed, 2 passed in 1.62s`：两个实体查询已退役原 pending，但完成回执未展示。
- `get_units`／`get_cities` 新增可选的本次确认回调；默认实体返回类型不变。仅当该查询令原动作终结时传递原回执，server 将它追加到现有文本结果的 `Action confirmation:` 段落。无持久确认缓存、跨 owner 状态或新生产工具。
- 完整纵向用例确认首次查询展示 `UNIT_CITY_ATTACK_CONFIRMED` 和终局标识、后续查询不重放；缺少终局证据时既不输出完成回执也不清 pending。两种查询仍报告真实相邻位置、同 owner、原生提交总数为一。另有两项公开 GameState 查询验证默认实体返回契约，六项通过。
- 原8文件加直接相关的 `test_server.py`、`test_game_state.py`、`test_session_commands.py`，共11文件 `713 passed in 49.11s`，无排除项；对应源码和测试编译、`git diff --check` 通过。
- 证据：`/private/tmp/civ6-terminal-review-fix-red.log`、`...green.log`、`...green-final.log`、`...targeted.log`。定向命令沿上述锁定 venv 和隔离 `PYTHONPATH` 形式，任务树改为 `terminal-capture-review-fix/civ6`，pytest cache 为 `/private/tmp/civ6-terminal-review-fix-pytest`。
- 未访问真实游戏或 CLI PTY，未热加载、重调攻击、清门禁或操作原终局／存档／日志；旧 owner 与 pending 保留限制不变。票保持 `claimed`，等待合并子代理和双轴复核，现场复核仍由主代理集中执行。

### 最终合并验收

`73a1e72` 及审查修复 `cc84abf` 均由合并子代理快进合入集成分支；最终 11 文件 713 项通过，无排除项。冻结 `cc84abf` 的 Standards／Spec 独立复核均为 No material issues found，原 P2 已消解。主代理只用原 CLI 的 pending 与 state overview 复核 T237 征服胜利；原 owner／pending、51 份检查点及元数据、受信基准、原始终局摘录和原日志前缀均保留。修复版未热加载到旧 owner，此限制按用户确认边界交付。
