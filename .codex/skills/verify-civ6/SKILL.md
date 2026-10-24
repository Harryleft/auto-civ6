---
name: verify-civ6
description: 验证 civ6 项目的知识 CLI、真实 stdio MCP、游戏状态查询、动作确认、回合推进和检查点恢复。用于代码变更后的验收、复现回归或收集运行证据；默认执行离线验证，实机路径按任务授权和受信存档执行。
---

# 验证 Civ6 MCP

主操作面是本机 stdio MCP，备用操作面是知识 CLI。先读[功能地图](features/README.md)，只选择本次改动或验收目标涉及的功能。项目边界以 [AGENTS.md](../../../AGENTS.md) 和[开发与验证](../../../docs/development.md)为准。

## 选择验证范围

| 任务范围 | 使用路径 | 可以证明什么 |
| --- | --- | --- |
| 知识查询、MCP 启动或协议变化 | [知识 CLI 与 MCP](features/knowledge.md) | 当前检出的真实 CLI 和 stdio MCP 可用、查询结果一致；无需游戏 |
| 生命周期、领域逻辑、Lua 或通信变化 | 下表中的离线测试及对应功能配方 | 隔离夹具中的行为；不代表游戏实测 |
| 明确授权的实机验收 | [状态](features/state.md)、[动作](features/actions.md)、[回合](features/turns.md)、[检查点](features/checkpoints.md) | 指定测试轮次中实际观察到的原生结果 |

未指定范围时，运行知识离线冒烟，并按本次 diff 选择测试。知识冒烟通过不能代替其他功能验收。请求「完整实机验收」但缺少授权范围或起点时，先完成离线部分，再报告缺项。

## Launch

从项目根目录执行 `uv sync --locked`，然后使用当前检出的依赖。`civ6` 离线 CLI 会按真实入口启动自己的 stdio MCP 子进程，完成后关闭。就绪要求知识 CLI 的 `validate` 返回 `valid`、MCP 初始化返回 `Civilization VI`，且工具列表包含两项知识查询。

```sh
uv run --locked --no-sync civ6 verify knowledge
```

命令使用当前知识包，默认新建 `.scratch/civ6/<run_id>/` 保存证据；`--output` 可指定一个尚不存在的目录。它只调用 `search_knowledge` 和 `get_knowledge`，不调用任何游戏工具。参数、领域测试及日志命令见[离线调试 CLI](../../../docs/cli.md)。

实机路径按[安装与连接](../../../docs/installation.md)和[保存与恢复](../../../docs/agent-recovery.md)启动。复用唯一活动控制客户端；只有任务已授权新开测试及替换现场，才加载指定[受信原始基准](../../../fixtures/saves/README.md)。续测只加载本轮已确认检查点。独立 worktree 不能隔离同一个游戏或 FireTuner 端口。

## Doctor

离线检查使用只读入口：

```sh
uv run --locked --no-sync civ6 doctor
```

缺依赖、包路径错误或知识指纹不符时停止该路径，保留错误原文。修复依赖按 `uv sync --locked`；知识包维护使用已有快照在新目录构建、比较差异后处理，不能用 `capture.py` 自动读取本机来源、刷新指纹掩盖未知差异。

实机 doctor 先调用 `get_game_overview`，再按配方读取 `get_units`、`get_cities` 或 `get_turn_status`。核对任务要求的世界身份、回合、玩家及就绪状态；端口可连接不能证明加载了正确对局。怀疑连接或状态异常时重做只读诊断，不自动重启或释放门禁。

## Drive

动作必须经过真实 CLI 或公开 MCP 工具；不使用 `run_lua`、内部 setter 或改写未决状态来制造成功。按地图选择具体入口并记录实参。实机动作的实体 ID、地块及目标来自本轮真实查询。

离线回归按影响范围选择，逐项保留命令、输出和退出码；下表不要求一次全跑。

| 修改范围 | 已有离线测试入口 |
| --- | --- |
| 知识与 stdio | `tests/test_knowledge_mcp.py`、`tests/test_knowledge_stdio.py` |
| 动作确认与门禁 | `tests/test_action_confirmation.py`、`tests/test_pending_dispatch.py` |
| 回合、身份与等待阶段 | `tests/test_end_turn.py`、`tests/test_turn_status.py` |
| 保存、替换与轮次 | `tests/test_game_lifecycle.py`、`tests/test_checkpoint_run.py` |
| 外交与来约 | `tests/test_trade_progress.py`、`tests/test_incoming_negotiation.py` |
| 通信 | `tests/test_connection.py`、`tests/test_tuner_client.py` |

例如动作门禁变更可运行 `uv run --locked pytest -q tests/test_action_confirmation.py tests/test_pending_dispatch.py`。这些测试使用现有隔离夹具；不要把 `scripts/test_connection.py`、`test_game_state.py`、`test_queries.py` 或 `integration_turns.py` 当作离线测试。

实机结果未知时记录原意图并停止依赖写入，先做只读核对；原入口只确认已有请求。禁止故障注入到未授权现场，也不以重复调用次数或等待时长判断失败。特殊阻塞按[特殊操作](../../../docs/agent-special-operations.md)处理。

## Evidence

- 每个功能记录：功能 ID、代码提交及未提交改动、模式、前置条件、入口、完整参数、原始输出、退出码或工具错误、前后状态、证据路径及结论。修复验收保留相同复现的失败前与通过后结果。
- CLI 自动保存 `context.json`、`cli-*.json`、MCP 初始化与工具列表、`mcp-attempts.json`、各响应、stderr 和 `summary.json`，并核对知识文件未变、临时目录已清理。运行结论在顶层 `status`，功能数据在 `data`。MCP 的标准本地动作日志仍写入 `~/.civ6-mcp/actions/`；保留这些日志，不清理其他会话的记录。
- 实机证据放在本轮 `games/<对局>/runs/<轮次>/evidence/`，报告放 `reports/`。命名与来源按[对局资料](../../../games/README.md)，不覆盖基准、旧记录或已有检查点。
- 查询要证明返回范围与实体；动作要同时证明单次提交和对应原生确认；保存要证明新鲜非空文件、SHA-256、身份回合和本轮归属。成功文案或截图单独不足以证明这些事实。
- `PASS` 只授予证据覆盖的功能和入口；`FAIL` 表示已观察到不符合预期；`BLOCKED` 表示前提或能力不足；`NOT_RUN` 表示未执行。列出离线通过、实机通过及未验证项。知识 helper 的 `PASS` 只覆盖知识 CLI 与 MCP。

## Cleanup

helper 用上下文管理器关闭自己创建的 MCP 管道及子进程，并删除自己的临时工作目录；证据目录和标准动作日志保留。失败同样执行清理，并检查证据仍存在。

实机结束时保留当前现场与本轮证据。只在任务允许且确属本轮启动时显式退出游戏；有未决请求时先按恢复指南保全现场。按进程名批量终止、删除存档或自动回滚对局都不属于清理。

## Helpers

`scripts/verify_knowledge.py` 保留为 `civ6 verify knowledge` 的兼容入口，逻辑统一位于项目包。使用 `uv run --locked python .codex/skills/verify-civ6/scripts/verify_knowledge.py` 可调用同一验证。非零退出时读取输出目录中的错误与 `summary.json`，修复有证据支持的问题后使用新目录重跑。它不修改 Git、游戏配置、存档或全局模型设置。

功能入口变化后，先核对本地图与源码，再更新受影响配方并实际跑通；可调用 `$pstack-maintain-verification-skill` 维护地图，实机部分仍按当前任务授权执行。
