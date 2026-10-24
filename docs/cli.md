# 离线调试 CLI

`civ6` 封装环境诊断、知识验证、领域测试和已有动作日志查询。所有业务命令只执行离线路径，不连接 FireTuner，不启动、加载或操作游戏；`civ-mcp` 仍是游戏 MCP 服务入口。

## 安装与调用

在源码项目根目录运行 `uv sync --locked`，然后使用 `uv run --locked civ6 ...`。也可使用同一解释器运行 `python -m civ_mcp.cli ...`。CLI 从当前目录向上查找项目，再尝试安装它的源码目录；使用 `--project PATH` 明确指定另一检出。doctor、知识验证及领域测试会拒绝加载了另一检出代码的解释器。

默认知识目录是所选项目的 `civ6_knowledge`。doctor 和知识验证可用 `--knowledge-dir PATH` 指定完整知识包；未采集本机游戏来源，不使用 `CIV6_KNOWLEDGE_DIR` 的隐含值选择验证对象。

## 命令

```sh
uv run --locked civ6 doctor
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

领域登记在 `src/civ_mcp/devtools/testing.py`：`knowledge`、`actions`、`turns`、`checkpoints`、`diplomacy`、`connection`。这是定向回归集合，不承诺覆盖该领域所有行为；修改影响范围超出集合时按[开发与验证](development.md)追加直接相关测试。

pytest 透传放在 `--` 后，支持 `-q`、`-v`、`-vv`、`-x`、`-k EXPR`、`-m EXPR`、`--maxfail N`、`--maxfail=N`、`--collect-only`、`--disable-warnings`。额外路径、插件和配置覆盖会被拒绝。测试子进程不继承 `PYTEST_ADDOPTS`、`PYTEST_PLUGINS`，并关闭第三方插件自动加载，保持登记的离线选择；pytest 与项目 conftest 仍正常加载。

日志仅支持当前 `GameLogger` 的 `timestamp/tool/params/result` 行格式。时间过滤必须带时区，边界包含。解析失败行计入 `malformed_lines`，残缺末行标记 `tail_incomplete`；它们不进入结果集。单次查询读取打开文件时已有的字节，不跟随新日志；`source_sha256` 是实际读取字节的指纹。`--limit` 为 1～1000，截断时 `truncated=true`。

## JSON 与退出码

业务命令 stdout 只输出一个 JSON 对象，默认即为 JSON，`--json` 可显式标注。`--help` 保留常规命令帮助。子进程原始 stdout/stderr 写入证据，不混进 stdout 的结果对象。

运行目录建立后，结果包含 `schema_version=1`、`command`、`run_id`、`mode=offline`、`status`、`exit_code`、起止时间、项目路径、Git 提交及脏状态、`data`、`errors` 和 `evidence`。`game_verification=not_run` 明确实机未测；命令成功只覆盖本命令实际执行的路径。输出及证据文件使用 UTF-8。

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
