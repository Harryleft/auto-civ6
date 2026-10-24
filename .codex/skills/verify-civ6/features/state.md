# 真实状态查询

确认连接对应正确世界，并读取模型实际可见的城市、单位和回合事实。

## Sub-features

- `state.overview`：回读当前文明、回合、产出和终局状态。
- `state.entities`：城市和单位 ID 可对应概览；隐藏、未知与不存在保持区别。
- `state.snapshot`、`state.identity`、`session.pending`：结构化快照的世界／玩家／回合、实体范围、服务身份及无未决上下文检查。

## How to get to it (user POV)

日常调用遵循[CLI 命令手册](../../../../docs/cli.md)。下文 MCP 工具名只表示底层入口；未封装工具用同一 debug 会话的 `tools`／`call` 调用。

- 持续 CLI 会话：`state overview`、`city list`、`unit list`。
- 已授权且已有唯一活动现场：`uv run --locked civ6 verify state --expect-turn TURN --expect-civilization CIVILIZATION_ID` 自动执行有界只读验收；前台会话内可用 `verify state` 复用当前上下文。完整命令及范围见[调试 CLI](../../../../docs/cli.md#自动实机只读验收)。
- 安装连接入口见[安装与连接](../../../../docs/installation.md)，只有任务要求启动游戏时才进入启动流程。

## 自动验收

核对任务要求的现场及控制通道后运行 `verify state`，读取顶层 `status`、`game_verification` 和 `data.verification`。`passed_readonly` 只覆盖结构化快照与会话检查；原始数据、逐项检查和 RPC 保留在证据中。有 pending 时先保留并按原入口确认，自动验收不得用实体查询消费它。读取失败、身份不符或范围未知不授予通过；该命令不加载、保存、推进或重启游戏。

## Driving it with civ6 MCP

Preconditions:

- 已授权查询真实对局；唯一活动控制客户端；任务指定的世界和轮次可核对。

- **概览。** 调用 `get_game_overview()`，保存完整响应。核对文明、回合及当前状态与任务指定起点或检查点一致；缺少关键身份事实时补充现有只读查询，不猜测。
- **实体。** 调用 `get_cities()` 和 `get_units()`，保留本次 ID、坐标及数量范围；与同一世界、回合的概览核对。初始尚未建城时可有零城，不能硬编码城市数量。
- **异常。** 查询失败或字段未知时记录具体缺口；按需调用 `get_turn_status()` 诊断。结果未确认前不把返回缓存当成当前成功证据。
- **证明。** 两组响应绑定同一任务和观察时点；`state.overview` 与 `state.entities` 分别判定。没有新鲜完整事实就不进入后续动作。

## Gotchas

- `127.0.0.1:4318` 开放不等于正确对局已就绪。端口测试不能代替 doctor。
- 正常状态查询可以刷新观察缓存，不能清除旧动作、保存或推进的未决门禁。
- 这个配方不加载、重启、推进或恢复对局。离线对应检查可选 `tests/test_fact_narration.py` 和 `tests/test_scope_observation_recovery.py`，不能据此标记实机通过。
