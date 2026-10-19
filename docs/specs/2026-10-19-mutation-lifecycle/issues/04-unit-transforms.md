# 04: 单位升级、晋升与删除的身份连续性

**What to build:** 对原单位进行升级、晋升或删除，明确实体替换／消失和多个副作用的完成范围。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 完整单位身份阻止低位 ID 复用；精确类型／晋升／移除证据确认。
- [x] 晋升的次数、回血与通知后续步骤不得因重入再次执行。
- [x] 延迟和丢回应只确认原操作，缺证据保持未知。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

## Answer

实现提交：`efc1ef3`；已合入集成分支 `68b194b`（merge `704eac4`）。

- `upgrade_unit` 保留原完整 ID，使用原生严格资格返回的真实升级类型，确认同一完整单位的类型变化、金币差额和 Expansion 2 的原生战略资源费用。实体替换而无关联证据保持未知。
- `promote_unit` 沿用 GameCore `SetPromotion`，补全目标职业／前置晋升资格；记录晋升、stored 清理、回血三个阶段，只对原单位执行一次。部分异常保留未决，重入只观察，不再次回血或清理。
- `unit_action(delete)` 保留完整 ID，要求本次命令 entered／returned、精确 `UnitRemovedFromMap` 事件和原单位 fresh absence；提交后不访问已删除 userdata，同低位新单位不参与旧操作。
- ActionLifecycle 增加独立 `submit_context`，默认 InGame，仅晋升选择 GameCore；确认 context 保持独立，仍使用同一未决所有者和 scope guard。
- 晋升不再扫描并修改全军 stored 或自动关闭通知。成功输出引导显式 `dismiss_popup`；该已有 UI 入口只在全军严格原生 PROMOTE 资格均为 false 时清 stale promotion 通知。其他单位可晋升或任一资格未知时保留通知，重复清理不重复回血、stored 或已关闭通知。
- 移除三个旧的无确认 Lua 提交 builder 和导出，避免留下平行写入路径。

验证：新增 52 项公开 MCP／GameState + 实际 Lua 5.1 测试；仅原生 API／连接 adapter 模拟。覆盖丢回应、取消、原生延迟、阶段异常、错误完整身份、拒绝零提交、世界切换、消费双证据、低 ID 删除重入及明确通知恢复。首轮相关回归 852 passed；合入 03／05 后组合回归 **857 passed**。`compileall -q src tests` 和 `git diff --check` 通过。未连接或修改真实对局。

合入后的组合命令：

```bash
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_unit_transforms.py tests/test_governor_confirmation.py tests/test_item_purchase_confirmation.py tests/test_game_state.py tests/test_pending_dispatch.py tests/test_tile_purchase_confirmation.py tests/test_game_lifecycle.py tests/test_server.py tests/test_action_scope.py tests/test_end_turn.py tests/test_contribution_popup_progress.py
```
