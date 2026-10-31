# 副作用预演

[返回 CLI 索引](../cli.md)

标准选项拼写为 `--dry-run`。可能改变游戏状态的命名命令直接追加该选项，例如先查询当前单位，再执行 `unit move 0 22 9 --dry-run`。CLI 校验命名参数及其映射后的真实输入 schema，返回 `event=dry_run`、原参数、`schema_valid=true`、`would_send=false`；未明确标为只读的工具按 `potentially_mutating=true` 展示。预演仍写本地证据。协议兼容诊断的 `call --dry-run TOOL JSON_OBJECT` 使用同一预演边界，不是普通对局的操作方式。

预演不发送目标 RPC、不查询游戏、不确认或清除 pending，也不检查实体是否存在、坐标可达、当前门禁或游戏前提。`scope=schema_only`、`pending_checked=false` 明确此范围；`local_policy_allows_execution` 只表示当前 offline／传输未知限制，不能当作游戏执行许可。可在 `debug --offline` 中预演游戏命令，实际调用仍被拒绝。

`quit --abandon --dry-run` 只读取服务内存，返回 `would_exit`、`would_lose_context` 并保留会话。真正 `quit --abandon` 仍显式丢弃进程上下文；有未决时返回 `BLOCKED`。预演没有自动执行、确认令牌或自动重试；实际执行须明确输入不带 `--dry-run` 的命令。

