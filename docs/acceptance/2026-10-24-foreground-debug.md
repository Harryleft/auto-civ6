# 前台 debug 会话离线验收

2026-10-24 在 macOS、Python 3.12.13、MCP SDK 1.26.0 下验证 `civ6 debug`。本次只运行离线路径与引擎替身，没有连接 FireTuner、启动或操作真实游戏。用法与退出规则见[调试 CLI](../cli.md#前台-debug-会话)。

## 行为与依据

| 验证对象 | 实际结果 |
| --- | --- |
| 服务身份与上下文 | 同一会话中的 UUID、PID 一致；多个命令复用同一 ClientSession 和 MCP 进程。身份变化会锁存失效，并阻止新的变更。 |
| pending 查看 | 新增 `get_runtime_status`，只在工具锁内读取公开所有者事实；重复读取不调用引擎、不消费动作确认读数，也不清保存／替换门禁。 |
| 动作未知后续确认 | 真实 MCP 调度与 GameState 使用脚本化引擎：首次移动返回未知，pending 和单位查询保留动作；再次相同意图确认成功，原生提交计数始终为 1。 |
| RPC 超时 | 原请求、参数和序号保留；同一进程可继续只读检查，新变更被拒绝。后续读超时不会覆盖首个传输未知。 |
| 退出 | 普通 quit 有 pending 时拒绝；显式 abandon 或管道 EOF 保存未决快照，返回 BLOCKED，关闭自有 MCP 并标记上下文丢失。没有进程恢复或意图重放。 |
| 交互终端 | POSIX 伪终端中 Ctrl+D 有 pending 时拒绝退出；后续仍能确认原请求。空闲与请求中的 Ctrl+C 不取消调用，不重复提交，也不更换子进程。 |
| 服务死亡 | 子进程主动退出、stdin 保持打开时，CLI 检测流关闭并退出，保留 MCP_CLOSED 失败；不会卡在输入线程或自动重启。 |
| 离线隔离 | `debug --offline` 的状态别名和游戏工具在发送前拒绝；控制台验收只实际调用知识工具与纯内存状态工具。 |
| 输入与返回 | JSON 重复键、非对象及非有限数字被拒绝；MCP 参数错误保留原返回，工具 schema 可查询，错误不重建会话。 |

替身入口为 `tests/fake_debug_server.py`：使用正式服务生命周期、工具调度及真实 GameState／ActionLifecycle，只替换外部引擎、只读辅助查询和日志接收端。测试专用慢请求、身份变化与进程退出工具不进入产品工具列表。

## 检查结果

- `civ6 test debug`：201 项通过，包含前台会话及服务协议测试。
- `tests/test_dev_cli.py`、`tests/test_pending_dispatch.py`、`tests/test_action_confirmation.py`、`tests/test_knowledge_mcp.py`、`tests/test_knowledge_stdio.py`：142 项通过。与上项无重复，共 343 项相关离线用例。
- 安装后的控制台入口实际运行 `debug --offline`、doctor 和知识验证，均正常结束；摘要与输出一致，进程身份保持，知识和状态证据在清理后保留。
- wheel 构建成功，包内 `server.py`、`cli.py`、`devtools/debug.py` 与受测源码字节一致；compileall、差异空白、技能元数据和本地文档引用检查通过。

控制台验收索引在本地忽略目录 `.scratch/civ6-debug-acceptance/c4ca8adf1bd14fd58a75f422fdb4c9fe/index.json`。该索引包含最终 debug 领域测试证据；另外 142 项回归证据在 `.scratch/civ6/ba588b919f374c7eac6622ffffa37c0f/`。原始日志、进程 UUID／PID 及本机路径只保留本地，不提交。

## 未验证与限制

未验证真实游戏的动作、回合和恢复，以及 Windows 实际控制台信号行为。普通 debug 会话不接管另一个 MCP 进程或恢复其未决意图。RPC 传输未知后的自动恢复不在本期范围；仅保留原请求并允许只读调查。证据快照不是跨进程意图存储，退出后的人工恢复仍按当前授权和已确认检查点执行。
