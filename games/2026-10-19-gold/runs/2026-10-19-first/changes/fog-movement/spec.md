# B06：普通移动缺少原生迷雾目标标志

Status: resolved

## Problem Statement

T31，战车由34,22移动到已探索但不可见的31,22，路径和CanStartOperation可用，但请求没有产生移动或segment事件，门禁持续未知。关闭商路界面、取消旧队列、关闭信息弹窗均不能解决。恢复同局后，相邻可见目标33,22移动正常。

## Solution

使用原生普通陆地移动支持迷雾目标的参数，不让已探索但不在视野中的正常战略目标被无声忽略；仍以实际位置和精确原生segment证据确认，不重发未知请求。

## User Stories

1. As a player, I want to move toward fogged destinations through native movement, so that exploration can proceed.
2. As a maintainer, I want the shipped UI fog movement flag used, so that path eligibility and submission use compatible parameters.
3. As a player, I want the same final parameters validated before submission, so that preflight checks the actual request.
4. As a player, I want movement confirmed from native effects, so that flags never substitute for completion.
5. As a player, I want lost responses confirmed once, so that an unresolved order is not replayed.
6. As a player, I want peace and hostile-capture boundaries preserved, so that exploration does not add offensive behavior.
7. As a maintainer, I want unavailable modifier definitions refused before submission, so that unsupported contexts remain explicit.
8. As a maintainer, I want full unit/world/turn/token gates preserved, so that unrelated movement does not confirm this action.
9. As a player, I want B05 interface cleanup and B06 movement evidence recorded separately, so that causes are not conflated.
10. As a player, I want same-run continuation and a confirmed next turn, so that repairs support the original game.

## Implementation Decisions

- Match the shipped MOVE_IGNORE_UNEXPLORED_DESTINATION modifier for ordinary move requests. Native ATTACK is separate: retain existing authorization only for verified current war targets; do not introduce offensive fallback, swap, coastal raid or ranged attack.
- Validate actual final params before arming/submission. Keep path, native identity, exact movement witness, unknown/no-replay and future-step handling intact. Do not infer movement from parameter acceptance.
- Missing/non-numeric flag/API remains explicit pre-submit refusal. Keep patch focused on movement params and corresponding public-seam tests.

## Testing Decisions

Existing already-approved GameState/MCP/Lua fixtures. Red→green case for fogged reachable destination requiring native ignore-fog flag; assert same params at preflight and one request. Retain peaceful/no-offensive and hostile capture tests, no-response no-replay and malformed-flag cases. Parent owns live same-run test toward a fogged destination and T31→T32/save. No new seam.

## Out of Scope

Relaxed movement proof, automatic retry, gate reset, new UI planner, omniscient strategy, forced movement, teleports or cheats.

## Further Notes

Original #430/434; after B05 #456/459; after correct oldqueue cancellation #467/470 still unknown. b05-selected-unit.txt proves correct selected fullID262146, SELECTION, readytrue, CanStarttrue, no queued goal. #474 proves31,22 visiblefalse/revealedtrue, 32,22 and33,22 visibletrue; flagsATTACK16/IGNORE16384. #475–478 same-run restored T31 trade→research→cancel→move tovisible33,22 succeeds. Shipped Civ6Common.lua RequestMoveOperation lines148–164 setsATTACK+IGNORE, with comment explicitly not to early-out for blocked fog destinations; current tool omits modifiers except authorized hostile targets. Root cause remains subject to live fog-target confirmation after patch. Parent will save current confirmedT31 beforefix and refresh MCP without replacing world.
