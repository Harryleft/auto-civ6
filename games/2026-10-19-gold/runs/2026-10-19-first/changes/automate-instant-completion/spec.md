# B04：自动探索已接纳但单位仍就绪

Status: resolved

## Problem Statement

T29，驻防战车切换自动探索后，精确任务开始见证已到达，entered/returned/started均true，但单位原生ready仍为true且位置不变；确认器强制要求非就绪，使已接纳的原生探索尝试永久未知。

## Solution

自动探索的完成契约只确认精确原生请求被接纳，不保证未来探索持续、移动或耗尽行动。支持引擎即时结束／无可用路线后单位仍就绪的情况，仍保留完整范围、单位与事件约束。

## User Stories

1. As a player, I want an accepted exploration request confirmed even when my unit remains ready, so that ordinary engine completion does not trap the game.
2. As a player, I want task acceptance separated from future movement, so that the tool promises only observed behavior.
3. As a maintainer, I want exact native operation evidence required, so that readiness alone cannot prove acceptance.
4. As a player, I want lost responses confirmed without another request, so that no duplicate exploration order is created.
5. As a maintainer, I want full unit/world/turn binding retained, so that stale or unrelated events cannot release gates.
6. As a player, I want unknown readiness or native read errors reported accurately, so that unreadable evidence is not invented.
7. As a maintainer, I want standing alert/heal/sleep and ALREADY no-op contracts preserved, so that an exploration fix stays scoped.
8. As a player, I want no automatic retry when exploration immediately returns ready, so that the model chooses its next action explicitly.
9. As a maintainer, I want the measured T29 event+ready state represented in public-seam Lua tests, so that this failure is reproducible.
10. As a player, I want same-run recovery, a pre-operation T29 checkpoint and T29→T30 verification, so that the original game continues after repair.

## Implementation Decisions

- Reuse existing task lifecycle and exact Started/Added witness interpretation.
- Distinguish exploration acceptance from mandatory persistent nonready state. Normal ready true/false are observations; neither alone establishes acceptance.
- No weakening of wrong identity, world, turn, callback, native-entry/return, cancellation or unknown-read gates; no auto-retry.
- Keep other task activity/no-op requirements intact; do not add exploration policy or native route guesses.

## Testing Decisions

Existing already-approved public GameState/MCP seam with native-Lua task fixtures. First add the T29 accepted+ready+awake+unchanged-position case as red, then fix minimally; maintain missing/wrong event and scope/no-replay tests, and ensure unrelated tasks do not inherit the relaxed readiness condition. Parent owns live reproduction/recovery. No new seam.

## Out of Scope

Background automation, new planner, claims that this proves continued exploration or an explored map, arbitrary gate resets, remote publication.

## Further Notes

Original #357, repeat #360. evidence/b04-native-automate.txt shows fullID262146, turn29, seed-1896272000; r.entered/returned/started=true, opIndex1/hash-1676983098; native ready=true, activityAWAKE1797587246, IsAutomated=false, IsReadyToAutomate=true, moves3, CanStartOperation(...false)=true. No movement occurred. Closing the MCP for diagnosis does not retire the old request as success; explicit same-run recovery from T25 will reproduce T29 and save a pre-attempt checkpoint before fixed-code testing.
