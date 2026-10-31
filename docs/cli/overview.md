# CLI 命令手册

[返回 CLI 索引](../cli.md)

本页说明安装、顶层入口和公共参数；会话与领域命令从 [CLI 索引](../cli.md) 按需展开。当前共有 149 个命名子命令（含 3 个既有双人热座实验命令），96 个基础 MCP 工具均有命名入口；任意 Lua 只在显式开发模式开放。`doctor`、`verify knowledge`、`test` 和 `logs` 不连接 FireTuner；`verify state` 明确访问当前真实游戏，普通 `debug` 仅在执行游戏查询或动作时访问游戏，`debug --offline` 拒绝这些调用。`civ-mcp` 保留为 CLI 内部服务，客户端不直接注册。

## 安装与调用

在源码项目根目录运行 `uv sync --locked`，然后使用 `uv run --locked civ6 ...`。也可使用同一解释器运行 `python -m civ_mcp.cli ...`。CLI 从当前目录向上查找项目，再尝试安装它的源码目录；使用 `--project PATH` 明确指定另一检出。doctor、知识验证及领域测试会拒绝加载了另一检出代码的解释器。

日常操作按以下路径执行：一次性诊断／验证／日志走顶层命令，连续游戏操作先启动一个 `civ6 debug` 并持续使用其 stdin。已有游戏控制会话时，在原会话内执行命名命令；不要为每个动作重启 CLI。`tools NAME` 和 `call TOOL JSON_OBJECT` 只作为同会话协议发现与兼容诊断。CLI 缺依赖或报错时按错误建议修复，不另开 MCP 客户端或直接调用内部函数。

| 公共参数 | 说明 |
| --- | --- |
| `--project PATH` | 指定源码检出；默认从 cwd 向上发现，再尝试安装源码位置。 |
| `--output PATH` | 新建证据目录；路径必须尚不存在，且不位于知识目录内。 |
| `--json` | 结果默认已是 JSON；与 `--help` 合用时返回机器可读帮助。 |
| `--help` | 显示当前层级帮助，不启动 MCP、不操作游戏。 |

默认知识目录是所选项目的 `civ6_knowledge`。doctor 和知识验证可用 `--knowledge-dir PATH` 指定完整知识包；未采集本机游戏来源，不使用 `CIV6_KNOWLEDGE_DIR` 的隐含值选择验证对象。

## 顶层命令

```sh
uv run --locked civ6 doctor
uv run --locked civ6 tuner status
uv run --locked civ6 debug --offline
uv run --locked civ6 verify knowledge
uv run --locked civ6 verify state
uv run --locked civ6 test --list
uv run --locked civ6 test actions
uv run --locked civ6 test knowledge --collect-only
uv run --locked civ6 test turns --timeout 300 -- -k pending -x
uv run --locked civ6 logs list --limit 10
uv run --locked civ6 logs show --file .scratch/action-sample.jsonl --tool get_units
uv run --locked civ6 logs timeline --file .scratch/action-sample.jsonl --since 2026-10-24T00:00:00+08:00
uv run --locked civ6 logs turns --relay .scratch/example-relay
```

日志示例中的文件是调用方提供的已有动作 JSONL，不由 CLI 自动创建。`logs list` 返回的 `session` 可代替 `--file`；用 `--dir PATH` 指定日志目录。

| 命令 | 执行内容与结果范围 |
| --- | --- |
| `doctor` | 检查 Python、实际代码目录、运行依赖、开发依赖、知识包完整性、真实 stdio MCP 初始化及工具发现；不调用游戏工具。缺少 pytest 或 lupa 记 `NOT_RUN`，不阻止知识路径。 |
| `tuner status` | 只读诊断：探测 FireTuner 端口状态，列出端口占用者（`lsof`）和本地 CLI／MCP 进程（`ps`），给出 `occupied`／`available`／`available_with_stale_processes`／`stale_session_likely`／`game_offline` 判定与处置提示；不发送游戏命令、不占用客户端连接。 |
| `verify knowledge` | 使用当前知识包验证搜索「写作」及 `TECH_WRITING` 的摘要、规则、证据读取；比较 CLI 与真实 MCP 返回，保留静态适用范围，核对知识文件未变。 |
| `verify state` | 对已运行游戏执行有界只读验收，核对世界身份、玩家／回合、实体数量与范围、同一 MCP 进程和无未决上下文；不启动、加载或推进游戏。 |
| `debug [--offline] [--timeout SECONDS]` | 启动一个前台 CLI 会话及其内部 MCP；从 stdin 接收命令，持续保留 pending。`--offline` 拒绝真实游戏调用。开发／热座选项见下文。 |
| `test --list` | 显示领域与测试文件，不运行测试。 |
| `test <领域>` | 只执行显式登记的 `tests/` 文件，pytest 原始退出码保留在 data.pytest_exit_code，CLI 自身使用下文统一退出码。`--collect-only` 只验证收集，`data.collection_only=true`，不代表测试用例已通过。 |
| `logs list` | 列出动作日志文件，按修改时间从新到旧排序；目录尚不存在时返回空列表。 |
| `logs show` | 保留文件顺序，返回最后 N 条匹配记录；按工具、结果子串、起止时间过滤。 |
| `logs timeline` | 返回时间最新的 N 条匹配记录，并按时间升序排列；不把日志文字解释为提交、确认或门禁状态。 |
| `logs turns --relay PATH` | 从已有前台转接器 `requests/` 凭据重建回合耗时，JSON 写入本次 CLI 证据目录；不连接游戏、不修改原始凭据。 |

### 顶层选项

| 命令 | 专用参数 |
| --- | --- |
| `doctor`、`verify knowledge` | `--knowledge-dir PATH`：完整知识包，默认 PROJECT/civ6_knowledge。 |
| `verify state` | `--expect-turn TURN`、`--expect-civilization CIVILIZATION_ID` 约束预期现场；`--timeout SECONDS` 为单次 RPC 预算，默认 300 秒。 |
| `debug` | `--offline` 禁止游戏调用；`--timeout SECONDS` 为单次 RPC 预算，默认 300 秒。 |
| `debug --compact` | stdout 只保留一份文本：当 MCP 的 `structuredContent` 仅含与唯一 text 块完全相同的 `result` 时，省略该重复字段；其他结构化字段、错误和多媒体照常返回。原始完整响应保留在 `debug-response-*.json`。默认输出契约不变。 |
| `tuner status` | 无专用参数；输出固定结构 JSON，证据写入本次 CLI 目录。 |
| `test` | DOMAIN 或 `--list`；`--collect-only` 只收集，`--timeout SECONDS` 为测试子进程预算（默认 300 秒）；pytest 选项放在 `--` 后。`--list` 不与领域或收集／运行选项组合。 |
| `logs list` | `--dir PATH` 指定动作日志目录；`--limit N` 默认 20，范围 1～1000。 |
| `logs show`、`logs timeline` | SESSION 或 `--file PATH` 二选一；`--dir PATH`、`--limit N`（默认 50）、`--tool NAME`、`--contains TEXT`、`--since TIME`、`--until TIME`。多个过滤条件按 AND 组合，contains 是 result 内的字面子串，时间含时区且边界包含。 |
| `logs turns` | `--relay PATH`：现有转接器证据目录；`--actors NAME ...`：限定参与者，默认排除 setup、repair、final、offline。 |

所有 timeout 必须是有限正数。日志默认目录为 `~/.civ6-mcp/actions/`，查询只读取现有文件，不跟随新日志。

领域登记在 `src/civ_mcp/devtools/testing.py`：`debug`、`knowledge`、`actions`、`turns`、`checkpoints`、`diplomacy`、`connection`。`civ6 test debug` 使用真实 stdio 和隔离的引擎替身验证会话，不访问游戏。这是定向回归集合，不承诺覆盖该领域所有行为；修改影响范围超出集合时按[开发与验证](../development.md)追加直接相关测试。

pytest 透传放在 `--` 后，支持 `-q`、`-v`、`-vv`、`-x`、`-k EXPR`、`-m EXPR`、`--maxfail N`、`--maxfail=N`、`--collect-only`、`--disable-warnings`。额外路径、插件和配置覆盖会被拒绝。测试子进程不继承 `PYTEST_ADDOPTS`、`PYTEST_PLUGINS`，并关闭第三方插件自动加载，保持登记的离线选择；pytest 与项目 conftest 仍正常加载。

日志仅支持当前 `GameLogger` 的 `timestamp/tool/params/result` 行格式。时间过滤必须带时区，边界包含。解析失败行计入 `malformed_lines`，残缺末行标记 `tail_incomplete`；它们不进入结果集。单次查询读取打开文件时已有的字节，不跟随新日志；`source_sha256` 是实际读取字节的指纹。`--limit` 为 1～1000，截断时 `truncated=true`。

