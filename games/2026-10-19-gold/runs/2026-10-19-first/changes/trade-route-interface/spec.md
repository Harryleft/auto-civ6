# B05：商路建立后的原生界面清理

Status: resolved

## Problem Statement

T31，商路已确认建立，但界面仍为MAKE_TRADE_ROUTE，随后战车移动没有见证、位置未变，弹窗关闭和原请求确认均无法释放门禁。原生TradeRouteChooser在RequestOperation后明确恢复SELECTION；工具未执行该步骤。

## Solution

完成商路操作对应的原生界面收尾，让后续普通动作正常执行。商路效果仍以精确路线确认；清理失败与路线建立分开报告，不重发经济操作，不以UI状态证明经济效果。

## User Stories

1. As a player, I want the route chooser closed after starting a route, so that later unit commands can execute.
2. As a maintainer, I want the stock UI transition reproduced, so that the native interface lifecycle is complete.
3. As a player, I want exact route and trader identities confirmed, so that cleanup cannot invent a successful trade.
4. As a player, I want lost responses confirmed without repeated route requests, so that no duplicate order is sent.
5. As a maintainer, I want cleanup failures retained explicitly, so that route acceptance and interface readiness remain distinguishable.
6. As a player, I want unrelated interface modes and pending diplomacy preserved, so that cleanup does not dismiss decisions.
7. As a maintainer, I want transfer-city behavior kept within its native lifecycle, so that this fix does not broaden effects.
8. As a player, I want same-run T30 recovery and T31→T32 verification, so that the original game continues.

## Implementation Decisions

- Reuse existing trader action lifecycle, exact city/unit/scope guards and route confirmation. Mirror the shipped chooser transition with minimal scope, handling read/transition failure honestly.
- No movement retry, no pending-gate reset, no generic UI auto-dismiss, no new strategic policy. Confirmed route result cannot substitute for confirmed subsequent movement.
- Check shipped route and origin chooser behavior before deciding transfer behavior. Parent owns live diagnostics and recovery.

## Testing Decisions

Same already-approved public GameState/MCP/Lua fixture seam. Add a failing trade-route chooser-mode case reflecting T31, fix minimally, retain unknown/no-replay/wrong-scope/route identity tests. Cover cleanup failure and unrelated modes appropriately. Offline tests plus live route→movement→T32 evidence. No new seam.

## Out of Scope

Global movement gate relaxation, arbitrary raw Lua writes, automatic route policy, background loops or remote publication.

## Further Notes

#428 route established (trader458752, Eridu131073→Uruk65536), #430 move cart262146 from34,22 to31,22 unknown; #434 still unknown after #433 popup dismissal. b05-native-move.txt shows no segment event or queued target, canMove=true, ready=true and CURRENT_MODE MAKE_TRADE_ROUTE -1756334557. Shipped TradeRouteChooser.lua lines835–836 requests then switchesSELECTION. Original MCP closed for read-only diagnostics; must explicitly recover same-run T30 (gold_20261019_t030_b04_verified), replay current development choices, save T31 before route, then verify fixed code without resending the old unknown request.
