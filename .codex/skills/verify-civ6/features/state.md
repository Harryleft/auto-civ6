# 真实状态查询

确认连接对应正确世界，并读取模型实际可见的城市、单位和回合事实。

## Sub-features

- `state.overview`：回读当前文明、回合、产出和终局状态。
- `state.entities`：城市和单位 ID 可对应概览；隐藏、未知与不存在保持区别。

## How to get to it (user POV)

- 已连接的 `civ6` MCP：`get_game_overview()`、`get_cities()`、`get_units()`。
- 安装连接入口见[安装与连接](../../../../docs/installation.md)，只有任务要求启动游戏时才进入启动流程。

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
