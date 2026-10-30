# 知识查询

无需运行游戏，即可通过知识 CLI 搜索规则并读取摘要、规则和来源证据；内部 MCP 由 CLI 管理。

## Sub-features

- `knowledge.cli`：当前知识包校验，搜索「写作」，读取 `TECH_WRITING` 的四个层级（summary、brief、rule、evidence）。
- `knowledge.mcp`：真实 stdio MCP 初始化、工具发现、搜索与分层读取；与 CLI 返回比较。

## How to get to it (user POV)

日常调用遵循[CLI 命令手册](../../../../docs/cli.md)，使用同一 debug 会话的命名命令。下文 MCP 名称只表示离线验收的内部协议。

- 在 `civ6 debug --offline` 会话执行 `knowledge search 写作 --topic technologies --limit 5`，再用 `knowledge show TECH_WRITING --level rule`；ID 取自本次搜索。
- 完整冒烟：从根目录运行 `uv run --locked --no-sync civ6 verify knowledge`。

## Driving it with CLI and stdio MCP

Preconditions:

- 已执行 `uv sync --locked`；解释器加载本次检出的 `src/civ_mcp`。
- `civ6_knowledge/query.py validate` 返回 `status=valid`，知识包与清单一致。

- **启动与诊断。** 运行完整冒烟；核对 `cli-doctor.json` 的退出码为 0、`mcp-initialize.json` 标识 `Civilization VI`、`mcp-tools.json` 有两个知识工具。
- **搜索。** 核对 CLI 与 MCP 的结果列表一致，且包含 `TECH_WRITING`；保留两侧原响应。
- **读取。** 对搜索结果依次调用 `get_knowledge(entry_id="TECH_WRITING", level="summary")`、`brief`、`rule`、`evidence`；核对同一条目、规则来源存在，并与 CLI 各层结果一致。brief 的公共字段适用于组内每行；精确还原与关键条件检查由定向测试覆盖。
- **范围。** MCP 回应必须保留 `applicability.current_session_verified=false`；静态知识不提供当前游戏操作资格。
- **收尾。** 退出码为 0，`summary.json` 顶层 `status=PASS`，`data` 中 `knowledge_unchanged`、`mcp_session_closed` 和 `scratch_removed` 均为 true；清理后各证据文件仍存在。

## Gotchas

- 打开端口或直接调用 Python 内部查询函数，不能证明 stdio 协议路径可用。
- 清单不匹配是知识包异常；保留失败证据，按[维护说明](../../../../civ6_knowledge/README.md#更新)在隔离目录重建比较，不直接覆盖当前来源。
- 空搜索不能证明规则不存在。语言、名称与内部 ID 子串的搜索语义见[知识说明](../../../../civ6_knowledge/README.md)。
- MCP 会写标准动作日志；helper 只清理自己的工作目录，不删除用户日志。
