# 08: 自动探索、治疗、警戒与休眠确认

**What to build:** 单位任务选择与原生活动／就绪状态一致后返回完成。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 绑定原单位全 ID，复用已有 readiness 语义，不暗改移动力。
- [x] 同意图仅确认，其他单位或帝国未决不能被绕过。
- [x] 取消、丢回应、延迟和原生能力缺失分别验证。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

## Answer

实现提交：`d82944f`；已合入集成分支 `30685ac`（merge `68e82cc`）。

- 四类任务共用既有 ActionLifecycle 和单位 readiness 读取，通过领域 `unit_tasks` 保存原完整 ID、操作 hash、世界与回合。提交前验证严格原生资格，缺能力／资格未知不试发。
- 原生 `UnitOperationStarted` 或 `UnitOperationAdded` 必须命中本次完整单位及准确操作；确认同时核对完整单位、非就绪和空闲 core。sleep 及可用的 heal／alert 原生活动枚举还需与实际活动一致，不用单独的 `ready=false` 冒充任意目标任务。
- 自动探索仅报告准确操作已启动，不虚构 automation getter 或探索结果。heal／alert 的可选活动枚举缺失时，输出范围同样限于精确原生事件证明的任务启动。
- 满血治疗，以及已处于准确 heal／alert／sleep 活动的非就绪单位，返回零提交 no-op。Runtime 不直接修改移动力，不尝试其他操作 fallback。
- MCP 保留完整 ID，沿 10 票通用条件让 owning entrypoint 首先匹配原意图；公开测试同时覆盖 full ID 和 legacy low ID，防止外层先确认再重发。移除四个旧直接提交 builder 和导出。

验证：新增 **48** 项公开 MCP／GameState＋真实 Lua 5.1 测试，模拟仅位于原生 API／连接 adapter。覆盖延迟、丢回应、取消、native throw、错操作事件、错活动、完整身份、资格与能力缺失、世界变更、只读确认失败、其他单位及帝国门禁。合入商路票后相关回归 **680 passed**；`compileall -q src tests`、`git diff --check` 通过。未连接或操作真实游戏。

```bash
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_unit_tasks.py tests/test_trader_confirmation.py tests/test_unit_readiness.py tests/test_pending_dispatch.py tests/test_server.py tests/test_game_state.py tests/test_tile_purchase_confirmation.py tests/test_unit_transforms.py tests/test_action_scope.py
```
