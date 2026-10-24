# 调试 CLI

`civ6` 提供离线诊断、验证、测试、日志查询，以及前台 `debug` 会话。前四类命令不连接 FireTuner；`debug` 只有收到明确工具调用才可能访问真实游戏。`civ-mcp` 仍是游戏 MCP 服务入口。

## 安装与调用

在源码项目根目录运行 `uv sync --locked`，然后使用 `uv run --locked civ6 ...`。也可使用同一解释器运行 `python -m civ_mcp.cli ...`。CLI 从当前目录向上查找项目，再尝试安装它的源码目录；使用 `--project PATH` 明确指定另一检出。doctor、知识验证及领域测试会拒绝加载了另一检出代码的解释器。

默认知识目录是所选项目的 `civ6_knowledge`。doctor 和知识验证可用 `--knowledge-dir PATH` 指定完整知识包；未采集本机游戏来源，不使用 `CIV6_KNOWLEDGE_DIR` 的隐含值选择验证对象。

## 命令

```sh
uv run --locked civ6 doctor
uv run --locked civ6 debug --offline
uv run --locked civ6 verify knowledge
uv run --locked civ6 test --list
uv run --locked civ6 test actions
uv run --locked civ6 test knowledge --collect-only
uv run --locked civ6 test turns --timeout 300 -- -k pending -x
uv run --locked civ6 logs list --limit 10
uv run --locked civ6 logs show --file .scratch/action-sample.jsonl --tool get_units
uv run --locked civ6 logs timeline --file .scratch/action-sample.jsonl --since 2026-10-24T00:00:00+08:00
```

日志示例中的文件是调用方提供的已有动作 JSONL，不由 CLI 自动创建。`logs list` 返回的 `session` 可代替 `--file`；用 `--dir PATH` 指定日志目录。

| 命令 | 执行内容与结果范围 |
| --- | --- |
| `doctor` | 检查 Python、实际代码目录、运行依赖、开发依赖、知识包完整性、真实 stdio MCP 初始化及工具发现；不调用游戏工具。缺少 pytest 或 lupa 记 `NOT_RUN`，不阻止知识路径。 |
| `verify knowledge` | 使用当前知识包验证搜索「写作」及 `TECH_WRITING` 的摘要、规则、证据读取；比较 CLI 与真实 MCP 返回，保留静态适用范围，核对知识文件未变。 |
| `test --list` | 显示领域与测试文件，不运行测试。 |
| `test <领域>` | 只执行显式登记的 `tests/` 文件，返回 pytest 原始退出码及证据。`--collect-only` 只验证收集，`data.collection_only=true`，不代表测试用例已通过。 |
| `logs list` | 列出动作日志文件，按修改时间从新到旧排序；目录尚不存在时返回空列表。 |
| `logs show` | 保留文件顺序，返回最后 N 条匹配记录；按工具、结果子串、起止时间过滤。 |
| `logs timeline` | 返回时间最新的 N 条匹配记录，并按时间升序排列；不把日志文字解释为提交、确认或门禁状态。 |

领域登记在 `src/civ_mcp/devtools/testing.py`：`debug`、`knowledge`、`actions`、`turns`、`checkpoints`、`diplomacy`、`connection`。`civ6 test debug` 使用真实 stdio 和隔离的引擎替身验证会话，不访问游戏。这是定向回归集合，不承诺覆盖该领域所有行为；修改影响范围超出集合时按[开发与验证](development.md)追加直接相关测试。

pytest 透传放在 `--` 后，支持 `-q`、`-v`、`-vv`、`-x`、`-k EXPR`、`-m EXPR`、`--maxfail N`、`--maxfail=N`、`--collect-only`、`--disable-warnings`。额外路径、插件和配置覆盖会被拒绝。测试子进程不继承 `PYTEST_ADDOPTS`、`PYTEST_PLUGINS`，并关闭第三方插件自动加载，保持登记的离线选择；pytest 与项目 conftest 仍正常加载。

日志仅支持当前 `GameLogger` 的 `timestamp/tool/params/result` 行格式。时间过滤必须带时区，边界包含。解析失败行计入 `malformed_lines`，残缺末行标记 `tail_incomplete`；它们不进入结果集。单次查询读取打开文件时已有的字节，不跟随新日志；`source_sha256` 是实际读取字节的指纹。`--limit` 为 1～1000，截断时 `truncated=true`。

## JSON 与退出码

一次性命令 stdout 只输出一个 JSON 对象；`debug` 输出逐行 JSON 事件，最后一行是会话摘要。默认即为 JSON，`--json` 可显式标注。`--help` 保留常规命令帮助。终端提示和 Ctrl+C 提示写 stderr；子进程原始 stderr 写入证据。

运行目录建立后，结果包含 `schema_version=1`、`command`、`run_id`、`mode=offline`、`status`、`exit_code`、起止时间、项目路径、Git 提交及脏状态、`data`、`errors` 和 `evidence`。`game_verification=not_run` 明确实机未测；命令成功只覆盖本命令实际执行的路径。输出及证据文件使用 UTF-8。

普通 debug 会话使用 `mode=debug`、`game_verification=unverified`，不把用户调试当作实机验收。`debug --offline` 保持 `mode=offline`、`game_verification=not_run`。

## 前台 debug 会话

运行 `uv run --locked civ6 debug` 后，整个前台会话只启动一个 MCP 子进程、初始化一次、使用同一个 ClientSession。启动只发现工具并读取服务内存，不自动查询、启动或加载游戏。退出不调用 `kill_game`。该命令不会接管已有 Codex MCP 的进程或未决请求；实机调试前须明确交接唯一控制通道，不能同时由两者控制游戏。

以下是会话内命令，写在终端输入或 stdin 中，不是独立 shell 命令：

```text
help
tools unit_action
pending
state overview
state units
state cities
state turn
call get_units {}
pending
quit
```

`state` 是对应公开 MCP 查询的别名。`call TOOL JSON_OBJECT` 只调用工具列表中实际存在的入口；省略参数等同 `{}`。JSON 不能有重复键、NaN 或 Infinity。工具参数继续由 MCP 校验，动作确认与门禁仍由现有 Runtime 所有者处理；CLI 不调用内部 setter 或清理未决状态。使用实体 ID 和坐标前须先查询真实状态。

`pending` 调用 `get_runtime_status`：返回本服务的 UUID、PID、连接标记、动作摘要与动作／推进／外交／议会／保存／替换的未决事实。该工具只读取所有者内存，不查询游戏、确认请求、刷新缓存或清门禁。`connected=false` 不表示世界不存在，`has_pending=false` 不证明别的 MCP 进程没有未决请求。

服务 UUID／PID 标识本次 MCP 生命周期，不是世界身份或测试轮次。每次显式工具调用前也读取同一内存快照核对服务身份，不自动确认任何请求。

`--offline` 只允许 `get_runtime_status`、`search_knowledge`、`get_knowledge`，本地 `help/tools/quit` 也可使用。其他工具在发送前拒绝，不能通过 `state` 或显式 `call` 绕过。离线检查可向会话输入知识查询和 pending，不必运行游戏。

会话异常与退出：

- Runtime 正常返回 `OUTCOME_UNKNOWN` 时，保持同一服务和原请求。先 `pending` 及只读核对，再按[恢复指南](agent-recovery.md)通过原入口确认；CLI 不自动重发。
- RPC 超时或丢回应时，额外保留本地 `transport_unknown`，后续只允许只读工具。空 pending、缓存刷新和后续读成功都不能清除这个标记；本版不自动恢复该次传输结果，也不重建 MCP 进程。
- `quit` 会新读 pending；有未决请求、传输未知或无法确认服务身份时拒绝退出，留在原会话。`quit --abandon` 是明确放弃当前进程内上下文的退出入口，仍返回 `BLOCKED`，不退役游戏中的原请求。保留证据后按已授权的检查点恢复流程处理，不能开一个空会话冒充已确认。
- 交互终端 Ctrl+D 有未决时返回提示；stdin 管道已耗尽时无法继续交互，保存未决快照、返回 `BLOCKED` 后关闭自有 MCP，`data.exit_reason=eof_unresolved`。该结束会丢失进程内上下文，不支持跨进程续接。
- Ctrl+C 不取消正在执行的 RPC，也不终止 MCP；等待请求返回后继续输入。每个 RPC 默认等待上限 300 秒，可用 `debug --timeout SECONDS` 调整。POSIX 子进程使用 SDK 的独立进程组；Windows 的实际控制台信号行为尚未实测。
- 服务 UUID／PID 变化会锁存失效并阻止后续变更。stdio 关闭时结束前台等待、保留失败证据；没有进程替换、自动恢复或重放。

`debug.jsonl` 记录命令、调用开始、原始返回、未知结果与退出原因；每条事件有递增序号，`tool_result.request` 对应 `tool_started.sequence`。完整 MCP 返回另存 `debug-response-*.json`。`runtime-status.json` 是最后一次成功读取的内存快照，`transport-unknown.json` 保留首个传输未知的原参数，后续读失败不会覆盖它。`debug-process.json`、`debug-initialize.json`、`debug-tools.json` 保存启动与协议事实。`data.tool_calls` 仅计显式调用和状态别名，不含内部的纯内存状态检查；日志查询的 session 文件名也不等于服务 UUID。

最后的 `event=session_closed` 摘要只判断会话是否正常结束；先前工具调用是否成功，应检查各自 `is_error` 和原始返回。明确放弃或异常关闭时 `data.context_lost=true`；证据文件不是可恢复的意图存储。

| 退出码 | 状态 | 含义 |
| --- | --- | --- |
| `0` | `PASS` | 本次操作成功；收集、列目录和执行测试的范围见 `data` |
| `1` | `FAIL` | 观察到验证不通过、测试失败、超时或执行异常 |
| `2` | `BLOCKED` | 参数、依赖、路径、权限或测试选择不满足条件；没有测试命中也属此类 |
| `130` | `BLOCKED` | 用户中断；检查已有证据后再决定是否重跑 |

## 证据与清理

默认目录为 `.scratch/civ6/<run_id>/`；`--output PATH` 可指定一个尚不存在的目录。全局参数可放在子命令之前或最终子命令之后。现有目录不会被覆盖，知识目录不能作为证据输出位置。

- `summary.json` 在执行期间标记 `RUNNING`；结束后与 stdout 的 JSON 对象相同，包括失败原因和部分完成的数据。
- `context.json` 保存解释器、Git 完整状态和运行层源码 SHA-256；stdout 只引用该文件，避免重复输出大量上下文。
- `cli-*.json`、`pytest.json` 保存完整命令、工作目录、起止时间、退出码、stdout/stderr。超时保留已收到的输出。
- 知识验证保存 MCP 子进程命令与显式环境覆盖、初始化、工具列表、`mcp-attempts.json`、每次原始响应和 stderr；doctor 只初始化与发现工具，不产生工具调用记录。
- 日志查询保存 `logs.json`，包括源指纹、筛选结果和坏行数量，不修改源日志。

知识验证只关闭自己创建的 MCP 管道及子进程，删除自己的临时工作目录，保留证据。MCP 的标准工具日志仍追加到 `~/.civ6-mcp/actions/`；不会删除其他会话的记录。

参数无法解析、项目无法定位或证据目录不能创建时，仍输出错误 JSON，但 `evidence=null`；这时没有可写入的运行目录。证据可能含本机路径和原始日志，默认留在忽略目录，不自动提交或外发。

`$verify-civ6` 使用此 CLI 执行知识验证；旧的技能 helper 继续作为兼容入口。实机验收仍按[保存与恢复](agent-recovery.md)及当前任务授权单独执行。
