# 开发与验证

修改、测试、审查或提交时读取本指南。实机验收另见本页对应章节；一般开发不需要加载游戏操作流程。

## 定位改动

先检查当前分支、工作树和本次目标。保留已有修改，按问题选择模块；涉及状态归属时再读[架构说明](architecture.md)，不要默认扫描历史设计。

| 修改对象 | 主要实现与相关验证 |
| --- | --- |
| MCP 参数、工具调度、整体串行和日志 | `server.py`；工具许可分类留在调度端，只消费公共未决事实。 |
| 领域准备、查询、完整存档替换 | `game_state.py`；通过真实 GameState／MCP 入口验证。 |
| 动作锁存、单次提交、回应与证据确认 | `actions.py` 的 ActionLifecycle；各动作保留自身原生见证。 |
| 推进请求、起始回合和差分基线 | `end_turn.py` 的 TurnProgress；报告读取失败不改变已确认推进。 |
| 当前外交语句、回应及交易阶段 | `diplomacy.py`、`lua/trade.py`；通过 GameState／MCP 与延迟原生事件验证，未知不重发。 |
| 原生菜单、文件事实、进程控制 | `game_lifecycle.py`、`game_launcher.py`；转换和进程结果用事实控制，不匹配展示文案。 |
| 检查点身份、指纹和测试轮次 | `checkpoints.py`；使用临时目录，保留文件与轮次约束。 |
| 游戏领域 Lua 构造与解析 | `lua/`；议会阶段与提案资格共用解释，纯查询不产生游戏操作。 |
| FireTuner 请求关联与上下文发现 | `connection.py`、`tuner_client.py`；单次执行、完整响应、超时隔离和无重放。 |

上述实现位于 `src/civ_mcp/`。保留领域工具名称和职责；优先集中重复知识，不以纯文件拆分或通用框架作为完成标准。

## 离线验证

代码修改只运行受影响模块的测试；由改动范围选择文件，例如交易生命周期修改：

```bash
uv sync --locked
uv run --locked pytest -q tests/test_trade_progress.py
uv run --locked python -m compileall -q src tests
git diff --check
```

影响跨模块契约时补充直接相关的测试文件，不默认运行全量。复用同一受测版本的集成结果；仅在新改动、失败或未解决疑点需要时重跑或扩大范围。

现有测试采用 GameState／FakeConn、内存 FireTuner 流、Lua 5.1 执行、快照差分和临时存档目录；在这些位置验证可观察行为，不复制自有状态机，也不以源码字符串断言代替关键时序验证。

纯文档修改检查本地链接及锚点、命令与实现、关键约束覆盖和 `git diff --check`；不为排版重跑游戏流程或全量测试。开发依赖和版本以 `pyproject.toml`、`uv.lock` 为准。

`scripts/test_connection.py`、`scripts/test_game_state.py`、`scripts/test_queries.py`、`scripts/integration_turns.py` 会连接真实游戏，不能作为离线检查运行。

## 实机验收

只有任务已授权操作真实游戏时才进入本节。运行前核对[唯一测试基准](../fixtures/saves/README.md)和[检查点规则](agent-recovery.md#检查点与测试轮次)，保护存档及已有证据。

`integration_turns.py` 通过 stdio MCP 调用同一工具实现。以下命令会替换当前对局；已有对局时，当前驱动会完整重启并加载原始基准：

```bash
uv run --locked python scripts/integration_turns.py --save benchmark_turn_1 --turns 10 --timeout 900
```

CLI 默认且仅允许 `benchmark_turn_1`，省略 `--save` 也从原始基准开始。当前驱动使用固定测试策略处理单位、科研文化和生产项；策略只属于测试驱动，不移入 Runtime。未知结果、挂起、不支持的阻塞或预算耗尽时退出，不自动恢复／重发。检查点续测和菜单换档验证通过相应 MCP 工具执行。

完成记录须包含使用的存档及来源、实际动作、净推进回合、阻塞与恢复、结果文件及未验证项。旧验收不能替代本次实测；只做离线验证时明确报告未连接游戏。

## 提交与交付

- 按目的组织提交，只纳入本次改动；沿用当前任务分支。任务要求集成分支时，保留单项提交和依赖工作项。
- 远程写入、push、合并主分支和改写历史按用户明确授权执行；不为文档同步切换已有任务分支。
- 报告提交 hash、验证结果和剩余阻碍；如创建了实施工作树，完成合入和必要验证后清理，保留所需分支与证据。
- 本项目已有本地 Markdown tracker；规格、依赖和完成记录随相关工作项维护。查找特定规格或验收时，从[记录索引](README.md#规格与验收)选择对应条目，不默认读取全部历史。
