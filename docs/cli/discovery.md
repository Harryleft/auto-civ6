# JSON 与退出码

[返回 CLI 索引](../cli.md)

已知命令时，直接用 `help GROUP COMMAND` 或 `GROUP COMMAND --help` 获取该命令的字段、边界与对应文档路径；未知命令先查 `help GROUP`。这些入口只生成本地帮助，不启动新的游戏查询，不必先通读 CLI 全部领域说明。

一次性命令 stdout 只输出一个 JSON 对象；`debug` 输出逐行 JSON 事件，最后一行是会话摘要。默认即为 JSON，`--json` 可显式标注。`--help` 显示当前层级的用途、参数、示例和结果范围；加 `--json` 返回结构化帮助（`kind=help`），只列直接子命令。help 不查找项目、不创建证据、不启动 MCP。终端提示和 Ctrl+C 提示写 stderr；子进程原始 stderr 写入证据。

运行目录建立后，结果包含 `schema_version=1`、`command`、`run_id`、`mode=offline`、`status`、`exit_code`、起止时间、项目路径、Git 提交及脏状态、`data`、`errors` 和 `evidence`。`game_verification=not_run` 明确实机未测；命令成功只覆盖本命令实际执行的路径。输出及证据文件使用 UTF-8。错误对象含 `code`、`message`、`hint`、`details`；`hint` 指出应修改的参数或下一项检查，工具 schema 错误的 `details.issues` 给出字段路径、校验规则和原因。

普通 debug 会话使用 `mode=debug`、`game_verification=unverified`，不把用户调试当作实机验收。`debug --offline` 保持 `mode=offline`、`game_verification=not_run`。

## 组合与发现

根 help 只展示命令组；逐层使用 `civ6 logs --help`、`civ6 logs show --help`。机器可用 `civ6 logs show --help --json` 读取同一参数契约。会话内 `help` 返回命名主题，`help unit`、`unit --help` 或 `unit move --help` 逐层查看命令及参数。只有协议兼容诊断才使用 `help call` 或 `tools NAME` 查看内部工具 schema。

一次性命令可将 JSON 结果交给 `jq` 等工具；组合脚本应同时检查退出码和 `status`。debug 接收 stdin 中每行一条命令，返回 JSONL；调用参数是原始 JSON 对象，不经过 shell 展开。筛选 `tool_result` 时按 `request` 关联 `tool_started.sequence`，不要把最后的会话 `PASS` 当作每次工具成功。

```sh
uv run --locked civ6 test --list | jq '.data.domains'
uv run --locked civ6 logs list | jq '.data.sessions[].session'
uv run --locked civ6 debug --offline <<'COMMANDS'
help unit
unit move --help
unit move 0 22 9 --dry-run
pending
quit
COMMANDS
```

## 会话内领域子命令

以下命令输入到已启动的 `civ6 debug` 会话，不是在 shell 中执行 `civ6 unit ...`。带空格的参数加引号，参数不经过 shell 展开。先用 `help`、`help unit`、`unit --help` 逐层发现，再用 `unit move --help` 查看实参。

表中「变更」命令均先加 `--dry-run` 预演，核实目标后再明确执行原命令；参数仍由真实 MCP schema 和 Runtime 校验。「只读」不等同无确认影响：普通实体查询可能顺带确认原动作；`pending` 仅看内存，`state snapshot` 则遇任一 pending 就拒绝进入游戏 I/O。

占城结果未知时，可在原会话执行 `unit list` 或 `city list`，由原 Runtime 所有者只读确认既有请求，再用 `pending` 核对门禁；不需要再次输入攻击命令。实体查询正文仍报告实际单位位置和城市状态。若此次查询确认了原请求，结果末尾追加 `Action confirmation:` 及原动作回执，仅显示一次；后续查询不重放，仍未决时不显示完成回执。攻城完成回执的可选 `completion:terminal_conquest` 表示本次近战占领已触发本方原生征服胜利，但攻击者仍在目标相邻格，不表示单位已进入城市。该例外仍要求原战斗、范围、城市归属和原生获胜队伍等证据完整；普通占领继续等待单位实际进城。

磁盘补丁不会替换存活服务内已冻结的确认查询。保留旧 owner、终局和未决请求时，实体查询只会运行该进程已有的确认器；不能把新版本的离线通过或另一个会话的空 pending 当作旧请求已确认。

