# 17: 议会打开、投票与提交门禁

**What to build:** 原生议会记录和 Python 门禁共同保留打开、逐项投票、提交及继续回合的阶段。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 部分投票不重发，既有阶段资格解释保留。
- [x] 其他入口和未注册直接提交方法不能绕过未决票计划。
- [x] 纯查询不投票；回合未决时仍可显式解除议会阻塞。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

实施提交：`cc36207`（原意图、精确原生 receipt、门禁与 native false 停止），`bedb8f8`（已证实零进入的打开拒绝释放）。已合入集成 `09a741a`，合并提交 `c62a00b`。

- 新增小型 `CongressProgress`，只保存 token、原票单 JSON、原世界/回合及观测状态；阶段仍由现有 Lua q/handler 驱动。议会可先 REGISTERED，再由显式 end_turn 触发原生阶段事件，并推进原生阶段/回合，因此不扩张普通 ActionLifecycle 的严格同回合规则。
- GameState/MCP 共享 `congress_pending` 和只读 `congress_gate()`。同意图只确认原请求；其他入口、保存/加载/任意 Lua 在未知期间受门禁；直接 vote_world_congress/submit_congress 要求改用完整显式计划，并移除无主 Lua 写入 builder。纯 get_world_congress 不触发或退休请求。
- `end_turn` 可用 `allow_registered_turn=True`：仅最新可信 REGISTERED 在保留原计划时放行；读回失败不会沿用旧登记状态。普通 TurnProgress 已未决时，议会操作仍需匹配其原世界。
- 投票、提交、继续任一原生调用 false/异常立即停止后续阶段；部分提交、取消、回应丢失不重放。continue 正常返回且原生阶段/下一回合实际推进才报告 WC_STAGE_FINISHED；allocation 为请求分配，不宣称实付 Favor 或投票胜负。
- 提交前复核世界、回合、阶段；换世界及已观测 scope 失效锁存，恢复旧世界也不追认。明确前置校验失败/零提交可释放。原生 UI 上下文临时缺失返回 WC_OPEN_REFUSED，不留下永久门禁。

验证：公开 MCP/GameState + 真实 Lua 5.1，仅模拟原生引擎及传输；新增 31 项故障/生命周期用例，相关回归 **976 passed**。包括 REGISTERED → 显式 end_turn → native handler → Congress continuation、已有 TurnProgress pending 的显式议会解除、不同阶段/世界、局部 false/异常、取消/丢回应、只读查询、无主直提交拒绝及回合推进后确认。执行 `PYTHONPATH=src` + 主仓 `.venv/bin/python -B -m pytest -q`：

`tests/test_congress_confirmation.py tests/test_congress_eligibility.py tests/test_congress_proposals_lua.py tests/test_congress_votes.py tests/test_congress_outcomes.py tests/test_world_congress_open.py tests/test_end_turn.py tests/test_end_turn_native.py tests/test_turn_status.py tests/test_server.py tests/test_game_state.py tests/test_action_confirmation.py tests/test_tile_purchase_confirmation.py tests/test_city_decisions.py`

`git diff --check` 与 Python 源码语法检查通过。未进行实机操作，未 push。门禁接口与 18 收口注意项已追加 `/tmp/civ6-mutation-congress-research.md`。
