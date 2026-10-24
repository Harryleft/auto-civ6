# CLI 组合、预演与发现验收

2026-10-24 在 macOS、Python 3.12.13、MCP SDK 1.26.0 下验证 CLI 接口改进；全程离线，没有连接 FireTuner 或操作真实游戏。用法见[调试 CLI](../cli.md)。

| 对象 | 验证结果 |
| --- | --- |
| 组合契约 | 保留单次命令 JSON、前台会话 JSONL、stderr 提示及退出码；stdin 每行一条会话命令。 |
| 分层发现 | 根 help 只列命令组，子命令 help 展示对应参数、示例及范围；`--help --json` 返回当前层级的结构化契约。help 不创建证据、不启动 MCP。会话支持 `help TOPIC`、`TOPIC --help`、`tools NAME`。 |
| 变更预演 | `call --dry-run` 校验真实 schema，返回原参数和 `schema_only` 计划；不发送目标 RPC，不消费引擎读数或确认 pending。缺少只读注解时按可能变更展示。 |
| 放弃预演 | `quit --abandon --dry-run` 读取服务内存并展示上下文丢失风险，保留同一会话。 |
| 真实 pending | 脚本化引擎中，未知动作之后预演相同意图不增加原生提交或确认动作；再次显式调用原入口才完成确认，原生提交总数为 1。 |
| 错误修正 | CLI 和会话错误保留 code/message，新增 hint/details；schema 错误指出字段路径及规则，日志错误指出应改的文件参数。pytest 失败返回具体领域、原退出码和证据修复建议。 |
| 离线边界 | `debug --offline` 可预演游戏工具，实际发送仍受原白名单约束。预演不承诺游戏前提满足；未知传输门禁和 Runtime 所有者保持原行为。 |

验证命令：

```sh
uv run --locked pytest -q tests/test_dev_cli.py tests/test_debug_cli.py tests/test_server.py
uv run --locked python -m compileall -q src tests
uv build --wheel --offline --out-dir .scratch/civ6-cli-contract-wheel
git diff --check
```

最终 229 项测试通过；测试输出保留在本地 `.scratch/civ6-cli-contract-final.txt`。独立 `civ6 test debug` 的 204 项通过证据在 `.scratch/civ6-cli-contract-debug/`，其后仅修正根 help 对 stdin 格式的描述，最终集合已覆盖此修正。结构化帮助输出保存在 `.scratch/civ6-cli-contract-help.json`。wheel 构建成功并核对 CLI 源码字节与依赖元数据；新增直接依赖使用原锁定版本，没有升级第三方包。compileall 和差异空白检查通过。

未验证真实游戏或 Windows 控制台行为。本次预演不执行游戏前提检查，不提供跨进程恢复，也不会自动将预演转成执行。
