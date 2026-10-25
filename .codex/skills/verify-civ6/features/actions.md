# 动作提交与确认

通过 CLI 发出一次明确动作，并核对对应原生确认及实际状态；未知时保留原意图。

## Sub-features

- `action.move`：从当前单位查询选择可移动单位，明确移动并回读实际位置。
- `action.pending`：丢回应或未完成时，原入口只确认已有动作，依赖写入保持门禁。

## How to get to it (user POV)

日常调用遵循[CLI 命令手册](../../../../docs/cli.md)，使用同一 debug 会话的命名命令；MCP 仅由 CLI 在内部管理。

- `unit list`、`map area X Y --radius N`、`unit path ID X Y` 获取本次对象和候选。
- `unit move ID X Y --dry-run` 预演，核实目标后明确执行 `unit move ID X Y`。
- 其他动作的资格与原生证据按[游戏操作](../../../../docs/agent-playbook.md#动作与回读)选择对应入口。

## 通过 CLI 验证

Preconditions:

- 状态配方通过，任务允许本次动作，存在可移动单位及已核实目标；没有阻止该动作的未决请求。

- **记录原意图。** 保存本次单位 ID、原坐标、行动状态、目标坐标、世界身份和回合；目标来自本次查询。
- **提交。** 调用一次 `unit move ID X Y`，记录完整实参和原始返回，不批量排队新动作。
- **回读。** 调用 `unit list` 核对实际位置。仅在原请求对应确认已完成且本次位置符合预期时，标记 `action.move` 通过；部分移动和最终抵达分别记录。
- **未知。** 若工具返回 `OUTCOME_UNKNOWN`，保留原请求，先只读查询；有确认需要时以完全相同意图进入原工具，仅确认已有请求。不能清门禁、换对象或改目标来重试。
- **门禁证据。** `action.pending` 优先用隔离测试 `uv run --locked pytest -q tests/test_action_confirmation.py tests/test_pending_dispatch.py` 验证；不要为了故障注入而断开真实游戏或重复写入。未发生真实未知场景时，实机此项记 `NOT_RUN`。

## Gotchas

- `MOVE_DEFERRED` 表示本次未提交；不能标为已抵达，也不反复提交相同意图。
- 只有位置变化、旧队列或成功文案不足以单独终结未决请求；保留原生确认与回读两种证据。
- 资格不足时记录原生拒绝，判定当前移动旅程前提不足；不要用 `skip` 替代移动验收。
- 游戏规则由引擎执行，策略由模型选择。验证技能不新增自动移动或自动重发策略。
