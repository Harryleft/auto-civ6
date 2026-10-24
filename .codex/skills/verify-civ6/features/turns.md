# 回合推进

确认一次结束回合请求到下一本地可行动回合的完整过程，并保留等待与阻塞事实。

## Sub-features

- `turn.advance`：在原世界中确认净推进及本地就绪。
- `turn.wait`：只读阶段诊断，阻塞处理后继续确认原请求，避免重复提交。

## How to get to it (user POV)

日常调用遵循[CLI 命令手册](../../../../docs/cli.md)。下文 MCP 工具名只表示底层入口；未封装工具用同一 debug 会话的 `tools`／`call` 调用。

- 同一 CLI 会话：`state overview` → `turn end --dry-run` → 明确执行 `turn end` → 按返回用 `turn status` 或具体领域命令。
- 仅用于指定原始基准专项验收的现有驱动（不是日常回合入口）：`uv run --locked python scripts/integration_turns.py --save benchmark_turn_1 --turns 1 --timeout 900`。

## Driving it with civ6 MCP

Preconditions:

- 已授权推进真实游戏，原世界和起始回合已确认，本地可操作；已有动作和保存未决按[恢复指南](../../../../docs/agent-recovery.md)处理。
- 只有任务指定 `benchmark_turn_1` 且明确允许替换当前现场时，才使用集成驱动。该驱动会加载原件，不能用于保留当前轮次的续测。

- **起点。** 保存概览、世界身份和起始回合 T，确认任务要求的起点。
- **提交。** 调用 `end_turn()` 一次；返回具体阻塞时按[特殊操作](../../../../docs/agent-special-operations.md)读取候选并作出任务授权内的明确选择。
- **等待。** 使用 `get_turn_status()` 保存阶段、就绪及累计等待。原推进未决时再次 `end_turn()` 只确认原请求，不发起新的回合意图。
- **确认。** 工具确认原世界中的后续本地可行动回合，再调用 `get_game_overview()` 回读；记录净推进和实际回合。提前终局另记实际结果，未达到约定推进量不标原目标通过。
- **证据。** 保留每次调用、阶段变化和最终概览。离线变更检查可选 `tests/test_end_turn.py`、`tests/test_turn_status.py`；不替代本次实机证据。

## Gotchas

- 回合数字增加不足以证明本地玩家就绪；普通概览也不能替换原请求的差分基线。
- 超时和阶段不变都不单独证明死锁。未知、挂起或未支持的阻塞停止写入并报告，不自动重启。
- 集成驱动只接受 `benchmark_turn_1`，固定策略只处理部分常见阻塞；不能借它验证另一张地图或完整外交／议会流程。
