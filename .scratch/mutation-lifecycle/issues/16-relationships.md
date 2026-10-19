# 16: 外交关系和宣战清理归属

**What to build:** 和平、友谊、使团、使馆、同盟、谴责和各类宣战都保留原请求及完成证据。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 关系回读绑定双方和原世界；延迟或丢回应无重复会话／回应。
- [x] OPEN_BORDERS 继续复用交易所有者，不重建可靠交易协议。
- [x] 宣战视图清理归属原世界／会话，不让延迟任务污染后续会话。
- [x] 来约接受／拒绝的直接提交 fallback 接入现有 DiplomacyProgress；已拥有的 outgoing counter 路径保持原协议。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

实施调研补充：respond_to_deal 只在有 outgoing attempt 时归属现有所有者，无 attempt 时的 incoming 分支仍直接 SendWorkingDeal。该遗漏在本外交票修复，18 票复核全入口覆盖；不新增协议或工作票。


## Answer

实现提交：`0c3b86a`；合入集成分支 `09a741a` 的合并提交：`bcbd024`。

- 和平、友谊、使团、使馆、同盟、谴责与八类宣战使用现有 `ActionLifecycle`。请求保留原世界／回合／双方／动作／会话，每个原生阶段先记录 entered、返回后记录 returned；中断后保留未知，重复调用只读确认。已满足的精确关系不再创建会话。
- 友谊和同盟继续依据原生 StateType／Hash 双向关系，同盟另核对精确类型；使团／使馆核对本方向存在及原生 gold cost 的精确扣款；和平／宣战核对双方战争状态；谴责要求本方原回合的新谴责记录。未知、旧读数或错误方向不触发第二次写入。
- 删除宣战延时后台清理与旧无归属来约提交 helper，视图沿已有显式 `dismiss_popup` 清理。`OPEN_BORDERS` 保持交易所有者；同盟不接受不相等的工作条款，也不向已有 pending deal 追加项目。
- 来约最终接受／拒绝复用 `DiplomacyProgress.IncomingAttempt` 与原生观察器，保留原始／当前条款、会话、动作及普通交易／AI 索取类型。接受要求原生请求正常返回与原会话的新 `HUMAN_ACCEPT_DEAL`；拒绝要求原请求和关闭阶段正常返回、原会话真实关闭。`MAKE_DEAL` 与 `MAKE_DEMAND` 分别匹配；既有 outgoing counter 与 incoming INSPECT／EQUALIZE 协议不变。
- 公共修正仅涉及已证明的单行 pre-entry refusal：直接释放自身 pending，不再把原生拒绝原因中的 `OUTCOME_UNKNOWN` 子串当作提交证据。真实公开入口验证了含该字样的原生拒绝仍为零提交并释放。
- seam 保持公开 MCP／GameState → 真实 owner → Lua 5.1，只模拟引擎与连接。旧测试的 None／dict／非字符串帧按真实 `GameConnection: list[str]` 契约由 adapter 抛 `LuaError`；保留空列表、畸形字符串、截断行、错误身份与丢回应覆盖，没有增加生产传输兼容层。

验证：合并后下列 19 个文件 **1,184 passed**，覆盖 MCP、共用契约、阶段取消／抛错／false、延迟、丢回应、错误会话／事件／世界、no-op、资源与目标双证据，以及原外交／交易／清理回归。另完成 `src`／`tests` 全部 Python AST 解析和 `git diff --check`。

```bash
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_relationship_lifecycle.py tests/test_friendship_confirmation_lua.py tests/test_access_confirmation_lua.py tests/test_alliance_confirmation_lua.py tests/test_incoming_final_response.py tests/test_incoming_negotiation.py tests/test_trade_progress.py tests/test_diplomacy_context_lua.py tests/test_diplomacy_cleanup.py tests/test_game_state.py tests/test_server.py tests/test_action_confirmation.py tests/test_action_lua_execution.py tests/test_action_scope.py tests/test_envoy_confirmation.py tests/test_tile_purchase_confirmation.py tests/test_governor_confirmation.py tests/test_player_choices.py tests/test_policy_lua_execution.py
```

边界：本票为离线真实 Lua／原生 adapter 验证，未连接真实游戏、未改动存档、未 push；跨重启持久化及人工并发不在规格内。原生阶段没有正常返回时，后续即便出现目标状态也保持未知，沿现有显式恢复路径处理。
