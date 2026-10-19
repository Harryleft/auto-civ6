# 07: 政策付费解锁与换卡的部分完成

**What to build:** 一次政策修改保存解锁与完整槽位计划的进度，直到目标槽位一致。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 已支付解锁不会因丢回应再次支付。
- [x] 部分槽位变化、延迟和资格拒绝有明确结果；不整段重发。
- [x] 原生槽位限制与现有政策行为回归通过。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

- 实施提交 `1dcacb2`；完成前合入集成分支 `942f796`，合并提交 `e51b712`。继续由 ActionLifecycle 持有一个未决请求，政策领域只保存完整计划和两次原生调用的进度，不新增通用事务系统。
- 首次操作前，原生回执保存原政府、槽位数量／类型、所有当前槽位与最终槽位、原始参数、支付成本与币种余额。保留现有挪卡、交换、NONE、未传入槽位、重复卡、原生槽位兼容与资格校验。
- 付费路径先单独进入 UNLOCK_POLICIES。原生调用正常返回、正确币种余额减少原成本、解锁成本为零且可提交状态可读后才记为已支付。原调用或同意图显式重入可继续尚未进入的 RequestPolicyChanges；confirm、普通查询和其他动作门禁始终只读，不推进阶段。
- 两个阶段均在原生调用前标记 entered，返回后标记 returned。取消／丢回应保留原计划；原生抛错不当成零提交；付款证据不全、部分槽位应用、政府／世界／回合／槽位结构变化保留 unknown。换卡阶段未进入但 API 暂缺时可等待恢复，不重新付款。
- 完成必须确认全部原最终槽位；已满足的完整计划 no-op。政策查询的 IsPolicyUnlocked 使用原生 Hash，CanSlotPolicy 保持 Index。
- `test_policy_lua_execution.py` **99 passed**；native adapter 真实扣 GOLD／FAITH、改变解锁与槽位事实，故意区分 Hash／Index。覆盖两个阶段的执行后丢回应、取消、原生 throw、原生 false、延迟、付款部分证据、完整计划部分应用、原意图顺序归一、后续资格失效、纯查询／跨入口门禁。
- 合并后相关 7 文件 **579 passed**；6 个改动 Python 文件 AST 解析及 `git diff --check` 通过。验证命令：`PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest tests/test_policy_lua_execution.py tests/test_game_state.py tests/test_server.py tests/test_pending_dispatch.py tests/test_player_choices.py tests/test_governor_confirmation.py tests/test_great_person_pool_confirmation.py -q --tb=short`。
- 仅离线验证；未连接实机、未 push。已进入但未返回的阶段继续保持未决，显式恢复沿既有边界处理。
