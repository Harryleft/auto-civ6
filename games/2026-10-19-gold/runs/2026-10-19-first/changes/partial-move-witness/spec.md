# B15：部分位移缺少 SegmentComplete 见证

Status: ready-for-agent

## Problem Statement

T69弓手786437从41,27向42,29移动，原生已到42,27且剩1移动力，但现有SegmentComplete见证始终false。#1270持续连接复现，#1273/#1275仅确认原请求，#1274关闭弹窗仍未知，阻塞后续护送和推进。

## Solution

采集此原生部分移动的真实事件，以精确本次请求、实体、身份和原生移动事实确认已经发生的片段，不承诺目标到达或将位置差单独作为成功。缺失、歧义和错误归属继续保留未知，重复确认不重发。

## User Stories

1. As a player, I want actual native partial movement confirmed, so that interrupted paths do not lock the game forever.
2. As a player, I want the actual current coordinate reported, so that I can decide the next movement.
3. As a player, I want arrival distinguished from partial movement, so that remaining path progress is not invented.
4. As a maintainer, I want a proven native event contract, so that a guessed callback is not treated as evidence.
5. As a maintainer, I want request/world/turn/player/full unit/origin/intent bound, so that unrelated movement cannot confirm this request.
6. As a maintainer, I want pre-entry, stale and mismatched events rejected, so that old movement is not reused.
7. As a player, I want repeated unknown requests to confirm only, so that no movement is sent twice.
8. As a maintainer, I want delayed or lost confirmation responses handled, so that witness cleanup does not lose later invalidation.
9. As a player, I want missing data kept unknown, so that read failures never authorize another action.
10. As a player, I want existing full movement and queue/cancel contracts preserved, so that ordinary moves remain compatible.
11. As a player, I want same-run T69 replay before verification, so that earlier choices and resources are preserved.
12. As a player, I want an explicit follow-up decision and T70 checkpoint, so that the repaired action demonstrably unblocks play.

## Implementation Decisions

- Inspect native shipped movement callbacks and capture one public MCP submission in the original T69 scenario before finalizing the alternative witness. No runtime policy, synthetic event or direct coordinate/MP setter.
- Prefer existing move ActionLifecycle and native Lua boundary. Keep owner/token/original scope/intent and one submit. Add only the minimal evidence required for this observed case; position delta alone remains insufficient.
- Track native submission entry/return if the new witness needs it. Native events must refer to the full original unit and valid original scope; known mismatch stays invalid, unknown remains unknown.
- Keep confirmed partial movement separate from arrival and retain later model decisions. No forced movement, teleport, automatic retry or alternate path selection in runtime.
- Parent owns live diagnosis/recovery. Current pending MCP will be closed only for read-only evidence, then explicit same-run gold_20261019_t069_before_archer_move restoration before further play. This checkpoint already includes CommercialHub, four-charge builder purchase and settler to42,26.

## Testing Decisions

Use already approved public GameState/MCP/native Lua fixtures, red→green the evidenced native partial move. Cover full-ID/owner/world/turn/token/intent mismatches, pre-entry and unrelated event, malformed/missing event data, no actual displacement, delayed/lost reply, repeat unknown without resubmit, explicit refusal and normal SegmentComplete behavior. Parent validates native partial outcome, separate subsequent unit decision and T69→T70 save.

## Out of Scope

Generic movement policy, weakening all movement to position-only success, forced pathing/embarkation, changing gameplay rules or unrelated query redesign.

## Further Notes

#1261 first reproduction was followed by a Node batch timeout; #1262 explicitly restored confirmed T69 and #1266–1268 faithfully replayed prior actions. #1269 gold_20261019_t069_before_archer_move is788358bytes. Persistent terminal MCP reproduces #1270, token222e6e5c3e2a45d3a164c06552e1a279, origin41,27/target42,29/current42,27/moves1/busyfalse/donefalse; repeated #1273/#1275 unchanged. Same run e8b4d6a62dca49bbb256e8264beb18f2, CIVILIZATION_SUMERIA/-1896272000/T69/player0. Raw original reset diagnostic t069-client-reset-move.txt; pending original process remains owned until explicit evidence/recovery steps.
