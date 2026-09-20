# DeepSeek Harness integration（v7 状态）

> **v7 M01 之后，DSH 不再是认知主路径。**
> 决策见 [v7 实施决策记录](../../docs/v7-decision-record.md) D4：`civ_agent` 是独立的
> 命令行 Python 进程，在进程内直接调用 `civ_mcp.runtime`，**不经 MCP，也不经 DSH**。
> 本文其余部分描述的是仍然存在、但仅供参照或实验使用的 overlay。

## 当前状态

M01 删除旧认知栈时，下列内容一并删除，本文不再描述它们：

- `civ6_belief_engine` 整个域包（信念 / 治理 / 预测 / 部门 / 图重放）；
- `civ_mcp.server`（旧 112 工具 MCP 面）与其 `pipeline` 门禁分类；
- `CIV_MCP_BELIEF_MODE` 与 `get_game_overview` 的 `RUNTIME POLICY` 块；
- `CIV_MCP_PLAY_PROFILE=legacy|lean` 与 `civ6.cordis.yml`、
  `civ6-agent.cordis.yml`、`civ6-agent-coding.cordis.yml`、`civ6-lean.cordis.yml`
  四个 overlay。

## 仍然保留的 overlay

[`civ6-runtime.cordis.yml`](civ6-runtime.cordis.yml) 是唯一保留的 overlay。它只启动
Runtime Core，不启动旧 server、不选择 play profile，也不拥有存档 / 读档 / FireTuner
生命周期：

```yaml
command: uv
args: [run, python, -m, civ_mcp.runtime.server]
env:
  CIV_MCP_RUNTIME_BRANCH: <host 提供的稳定 branch token>
  CIV_MCP_RUNTIME_STORE: <可选 SQLite 路径>
```

由 [`scripts/runtime_dsh`](../../scripts/runtime_dsh) 拉起：

```bash
./scripts/runtime_dsh --branch <host-token> [--store <sqlite-path>] [--headless] [--dry-run]
```

前置条件：Civ6 已进入对局且 `EnableTuner=1`；4318 正在监听且**没有**已建立的客户端
（FireTuner 单客户端）；`DEEPSEEK_HARNESS_DIR` 指向已构建的 DSH checkout。

## 与 v7 主路径的关系

MVP 不需要 DSH。要跑一局，使用 `civ_agent` 的命令行入口（launcher 任务卡落地后提供），
它自己完成：安装基准存档 → 启动游戏 → 读档 → 运行 LangGraph 循环 → 追加 Game Memory。

本 overlay 保留的原因是它把 Runtime 的绑定契约（branch token、store 路径、单客户端
约束）固化成了可检查的配置，`tests/test_runtime_entrypoint.py` 会核对它。
