# Codex Hook 工作流与质量检查

仓库级配置是 [`.codex/hooks.json`](../.codex/hooks.json)，实现为 [`scripts/codex_hooks.py`](../scripts/codex_hooks.py)。它只执行离线 Git 查询和 Python 编译检查，运行时状态保存在忽略目录 `.scratch/codex-hooks/<session-hash>/`。不启动 Runtime、不连接游戏、不运行测试代码，不修改全局 Hook。

## 已配置的工作流

| 事件 | 执行内容 | 返回与影响 |
| --- | --- | --- |
| `SessionStart` | 记录当前项目文件的内容指纹和 Git 基准；提示按任务读取开发／游戏指南 | 注入简短上下文；恢复、压缩后沿用原基线 |
| `Stop` | 比较会话基线，对变化的 Python 文件执行 `compile`，检查变化文件的 diff 空白问题，生成定向测试建议 | 新失败返回一次 `decision=block`，让 Codex 继续修复；继续回合不再阻止结束 |

内容指纹覆盖 `src/`、`tests/`、`scripts/`、`docs/`、`civ6_knowledge/` 下的 `.py`、`.md`、`.toml`、`.json`、`.yml`、`.yaml`、`.lock` 文件，以及同类根目录文件和 `.codex/hooks.json`。不扫描对局证据、存档、个人配置或历史会话。

- 会话开始前已有且随后未变的脏文件不进入本次检查。若本会话继续修改同一文件，则检查该文件当前完整 Python 语法，以及相对起始 Git 提交的空白问题；同一文件中更早的空白问题也可能被报告。
- staged、未跟踪文件和会话内已提交的变化都按内容识别。删除的文件不做编译检查；Python 符号链接标为 `NOT_RUN` 并报告，避免读取链接目标。
- 相同内容指纹复用结果；变化后重新检查。`Stop` 收到 `stop_hook_active=true` 时只报告，不再要求继续，避免循环。
- 首次 `Stop` 缺少 `SessionStart` 基线时，仅建立基线并报告 `NOT_RUN`；初始化、Git 或文件错误报告 `BLOCKED`，不伪造通过。

## 启用与停用

1. 在项目根目录执行 `uv sync --locked`，准备 `.venv`。配置在 macOS／Linux 使用项目解释器，Windows 配置提供 PowerShell 启动命令。
2. 确认项目已受信，重新打开或恢复本地 Codex 会话。在 CLI 使用 `/hooks` 查看项目来源，审阅并信任新增的两项 Hook。新增或改变 Hook 定义后需重新审阅。
3. 开始一次正常修改；回合结束后在 `.scratch/codex-hooks/` 查看 `report.json`。

需要停止本项目 Hook 时，在 `/hooks` 禁用对应项，或移走本项目 `.codex/hooks.json`。其他全局 Hook 独立加载；仓库配置不会覆盖全局配置。

配置通过 `git rev-parse --show-toplevel` 定位脚本，因此从仓库子目录启动也可使用。尚未准备 `.venv` 时，启动命令会失败，应先安装依赖。命令 Hook 在本地编排的 Codex 会话中使用；云端编排即便有本地执行环境，也不支持这类本地命令 Hook。平台行为以 [OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)为准。

## 质量门槛

`report.json` 的 `PASS` 仅代表本次变化文件的 Python 语法和 diff 空白检查通过。无变化为 `NOT_RUN`；文件系统等前提错误不会获得通过。报告始终标记 `behavior_tests=NOT_RUN`、`game_verification=not_run`，不能代替领域测试、静态类型检查、格式化器、完整文档校验或实机验收。

建议命令不会由 Hook 执行。修改后的测试文件建议对应 pytest 文件；常见核心模块建议现有 `civ6 test` 领域；CLI／MCP／知识变化建议 `verify knowledge`；依赖变化建议 `doctor`。这只是路由提示，修改 `game_state.py`、Lua 或跨模块契约等内容时，仍需按[开发与验证](development.md)选择直接相关测试。

| 常见工作流 | 适合的 Hook 事件 | 当前处理 |
| --- | --- | --- |
| 新会话、恢复或压缩后恢复项目上下文 | `SessionStart` | 已配置 |
| 交付前做轻量确定性检查 | `Stop` | 已配置 |
| 每次编辑后格式化或定向测试 | `PostToolUse` | 暂不配置，避免频繁重复运行或自动修改文件 |
| 提交前完整行为验证 | 测试命令与 CI | 由改动范围决定，不在 `Stop` 隐式全量执行 |
| 操作游戏、推进回合、未知结果恢复 | 原持续 CLI 会话 | 按任务授权和游戏状态处理，Hook 不接管策略或 pending |

## 验证与维护

```sh
uv run --locked pytest -q tests/test_codex_hooks.py
git diff --check
```

测试使用隔离 Git 仓库，通过真实脚本进程覆盖语法错误、未跟踪文件空白问题、已提交变化、既有脏文件隔离、子目录启动、恢复／压缩、会话隔离、缺失基线和避免重复继续。它不调用模型、不启动游戏，也不证明桌面端已信任或实际触发配置。macOS 的命令启动可本地验收；Windows 启动命令需在对应平台另行验证。

状态和报告保留在 `.scratch/`，不提交。仅在不再需要对应会话证据时删除该会话目录；不要通过删除状态来把未执行检查改写为通过。

2026-10-26 在 macOS、Codex CLI `0.161.0` 验证：13 项隔离测试通过；配置中两条实际启动命令的 JSON 往返通过；原生 `hooks/list` 识别 `sessionStart` 和 `stop` 两项 project Hook，`enabled=true`、`trust=untrusted`，无错误或警告。安装证据为 `.scratch/codex-hooks/install-verification.json` 和 `native-discovery.json`。尚未验收信任后的桌面自动触发及 Windows 启动。
