# 前台 debug 会话

[返回 CLI 索引](../cli.md)

运行 `uv run --locked civ6 debug` 后，整个前台会话只启动一个 MCP 子进程、初始化一次、使用同一个 ClientSession。启动只发现工具并读取服务内存，不自动查询、启动或加载游戏。退出不调用 `kill_game`。该命令不会接管旧客户端服务的进程或未决请求；旧注册移除后仍须先确认其请求并正常结束原所有者。

会话控制命令如下；它们不是独立 shell 子命令：

| 会话命令 | 行为 |
| --- | --- |
| `help [TOPIC]`、`TOPIC --help` | 分层帮助，返回 JSON 事件。 |
| `tools [NAME]` | 列工具名，或读取一个工具的真实输入 schema。 |
| `pending` | 读取本 MCP 的内存 pending 与本地 transport_unknown。 |
| `state overview`、`state units`、`state cities`、`state turn`、`state snapshot` | 概览、单位、城市、回合与结构化快照。 |
| `verify state` | 在当前会话执行自动实机只读验收。 |
| `call [--dry-run] TOOL [JSON_OBJECT]` | 同会话协议兼容入口；普通对局使用命名命令，省略 JSON 等同 {}。 |
| `quit [--abandon] [--dry-run]` | 正常退出、预览退出或显式放弃上下文。 |


`state` 是对应公开 MCP 查询的别名，`state snapshot` 返回结构化当前玩家快照。`call TOOL JSON_OBJECT` 只调用工具列表中实际存在的入口；省略参数等同 `{}`。JSON 不能有重复键、NaN 或 Infinity。CLI 按实际发现的 `inputSchema` 在发送前校验参数，MCP 仍执行服务端校验；动作确认与门禁仍由现有 Runtime 所有者处理；CLI 不调用内部 setter 或清理未决状态。使用实体 ID 和坐标前须先查询真实状态。

`pending` 调用 `get_runtime_status`：返回本服务的 UUID、PID、连接标记、动作摘要与动作／推进／外交／议会／保存／替换的未决事实。该工具只读取所有者内存，不查询游戏、确认请求、刷新缓存或清门禁。`connected=false` 不表示世界不存在，`has_pending=false` 不证明别的 MCP 进程没有未决请求。

服务 UUID／PID 标识本次 MCP 生命周期，不是世界身份或测试轮次。每次显式工具调用前也读取同一内存快照核对服务身份，不自动确认任何请求。

`--offline` 只允许发送 `get_runtime_status`、`search_knowledge`、`search_research`、`get_knowledge`，本地 `help/tools/quit` 和其他工具的 dry-run 也可使用。其他工具的实际调用在发送前拒绝，不能通过 `state` 或显式 `call` 绕过。离线检查可向会话输入知识查询和 pending，不必运行游戏。

会话异常与退出：

- Runtime 正常返回 `OUTCOME_UNKNOWN` 时，保持同一服务和原请求。先 `pending` 及只读核对，再按[恢复指南](../agent-recovery.md)通过原入口确认；CLI 不自动重发。
- RPC 超时或丢回应时，额外保留本地 `transport_unknown`，后续只允许只读工具。空 pending、缓存刷新和后续读成功都不能清除这个标记；本版不自动恢复该次传输结果，也不重建 MCP 进程。
- 游戏侧只读工具有有界预算（45 秒）：主菜单、加载中或模态界面阻塞时，工具返回 `ERR:TOOL_TIMEOUT`（未提交、未确认、未重放任何内容），CLI 不再等到 300 秒 RPC 超时并锁存 `transport_unknown`。用 `civ6 tuner status` 区分“游戏不可达／连接被占用／残留会话”，再决定加载或恢复。
- `quit` 会新读 pending；有未决请求、传输未知或无法确认服务身份时拒绝退出，留在原会话。`quit --abandon` 是明确放弃当前进程内上下文的退出入口，仍返回 `BLOCKED`（`context_lost=true`、`exit_reason=abandoned`），不退役游戏中的原请求；传输未知导致无法确认时，这是 CLI 内的显式收尾路径。保留证据后按已授权的检查点恢复流程处理，不能开一个空会话冒充已确认。
- 交互终端 Ctrl+D 有未决时返回提示；stdin 管道已耗尽时无法继续交互，保存未决快照、返回 `BLOCKED` 后关闭自有 MCP，`data.exit_reason=eof_unresolved`。该结束会丢失进程内上下文，不支持跨进程续接。
- Ctrl+C 的设计行为是不取消正在执行的 RPC，也不终止 MCP；等待请求返回后继续输入。每个 RPC 默认等待上限 300 秒，可用 `debug --timeout SECONDS` 调整。POSIX 子进程使用 SDK 的独立进程组。Windows 终端中断导致的空读会继续读取原输入流；Windows 11／Python 3.12.2／UU 真终端已验证连续两次空闲 Ctrl+C 后保持同一 PID／session ID，继续查询并显式退出，见 [Windows 11 修复复验](../acceptance/2026-10-27-windows11-cli-fixes.md)。实际 Windows 游戏 RPC 中断行为仍未验证。
- 服务 UUID／PID 变化会锁存失效并阻止后续变更。stdio 关闭时结束前台等待、保留失败证据；没有进程替换、自动恢复或重放。

`debug.jsonl` 记录命令、调用开始、原始返回、未知结果与退出原因；每条事件有递增序号，`tool_result.request` 对应 `tool_started.sequence`。完整 MCP 返回另存 `debug-response-*.json`。`runtime-status.json` 是最后一次成功读取的内存快照，`transport-unknown.json` 保留首个传输未知的原参数，后续读失败不会覆盖它。`debug-process.json`、`debug-initialize.json`、`debug-tools.json` 保存启动与协议事实。`data.tool_calls` 仅计显式调用和状态别名，不含内部的纯内存状态检查；日志查询的 session 文件名也不等于服务 UUID。

最后的 `event=session_closed` 摘要只判断会话是否正常结束；先前工具调用是否成功，应检查各自 `is_error` 和原始返回。`tool_result.is_error` 识别协议错误及服务显式的 `Error:`／`ERR:` 文本错误边界；`protocol_is_error` 保留原 MCP 标志，`error` 给出错误码、来源和修正建议，`result` 保留未改写回包。原生资格拒绝也应读取并处理；没有错误标志不等同动作已确认，CLI 不由其他展示文案推断成功或释放 pending。明确放弃或异常关闭时 `data.context_lost=true`；证据文件不是可恢复的意图存储。

| 退出码 | 状态 | 含义 |
| --- | --- | --- |
| `0` | `PASS` | 本次操作成功；收集、列目录和执行测试的范围见 `data` |
| `1` | `FAIL` | 观察到验证不通过、测试失败、超时或执行异常 |
| `2` | `BLOCKED` | 参数、依赖、路径、权限或测试选择不满足条件；没有测试命中也属此类 |
| `130` | `BLOCKED` | 用户中断；检查已有证据后再决定是否重跑 |

