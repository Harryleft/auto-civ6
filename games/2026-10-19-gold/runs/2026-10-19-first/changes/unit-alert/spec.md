# B03：原生警戒任务开始与活动状态

Status: ready-for-agent

## Problem Statement

T16，战车警戒收到精确的原生开始见证，但确认器要求活动状态等于 SENTRY，本机实际为 OPERATION；原请求持续未知并阻断游戏。

## Solution

正确区分原生任务开始与长期活动状态：新警戒请求在完整范围、单次提交、精确开始见证及原生非就绪等证据充分时确认开始；已处于目标状态的零提交分支继续严格验证。

## User Stories

1. As a player, I want a started alert operation confirmed, so that gameplay can continue.
2. As a player, I want native OPERATION distinguished from final SENTRY, so that normal task activity is not mistaken for failure.
3. As a maintainer, I want the exact task event retained, so that a generic nonready unit cannot confirm an unrelated request.
4. As a player, I want retries to confirm only the original, so that lost replies do not duplicate orders.
5. As a maintainer, I want already-satisfied no-ops strictly checked, so that they never claim a new task started.
6. As a player, I want full unit identity and original world/turn binding, so that unrelated units or worlds cannot release gates.
7. As a maintainer, I want healing and sleep boundaries preserved, so that a sentry repair does not weaken other tasks.
8. As a player, I want ordinary final task results left to future turns, so that confirmation does not promise outcomes it has not measured.
9. As a maintainer, I want offline Lua tests using the real OPERATION state, so that this regression is reproducible.
10. As a player, I want a confirmed same-run pre-alert checkpoint and subsequent T16→T17 verification, so that the fix returns to the original game.

## Implementation Decisions

- Stay inside the existing task confirmation module; retain ActionLifecycle and all pending gates.
- Use the measured native activity contract and shipped UI treatment of ACTIVITY_OPERATION, not display strings or arbitrary truthy values.
- Separate already-satisfied/no-new-request verification from freshly started task confirmation. Do not accept other activities without reason.

## Testing Decisions

Existing user-confirmed GameState/MCP seam with Lua-backed unit-task tests. Add the real alert started+nonready+OPERATION case first (red), then minimal correction; keep missing/wrong event, wrong scope, wrong ID, cancellation and no-op behavior. Parent performs live same-run restore and T16→T17. No new seam.

## Out of Scope

AI strategy, periodic background processes, changes to game rules/resources, broad refactors or remote publication.

## Further Notes

Original #163 at T16; repeated #166 still unknown after HistoricMoments dismissal. evidence/b03-native-alert.txt: entered=true returned=true started=true; fullID262146; opHash-448283625 / rowIndex54; expected SENTRY -793848223, actual OPERATION1225574625; IsReadyToMove=false, core busy=false. Recovery checkpoint currently T12 gold_20261019_t012_b02_verified; parent will reproduce original choices and save a pre-alert T16 checkpoint.
