# B02：自动探索的真实原生开始确认

Status: ready-for-agent

## Problem Statement

神级苏美尔 T11，侦察兵自动探索实际移动后，工具的任务见证始终缺少 started，重复确认仍未知，全局门禁持续阻塞。请求只提交一次；弹窗清理不能解除。

## Solution

根据本机真实事件与原生契约确认自动探索请求的开始，保持单次提交和完整世界／回合／单位绑定；不以位移、剩余行动或普通返回值冒充开始证据。

## User Stories

1. As a player, I want automate exploration to confirm native acceptance, so that gameplay can continue.
2. As a player, I want one original request preserved after unknown responses, so that confirming it never creates another exploration order.
3. As a maintainer, I want actual shipped native event contracts used, so that fake tests reflect the real game.
4. As a player, I want exact full unit identity and world binding, so that unrelated events cannot release a pending action.
5. As a player, I want delayed events to remain confirmable, so that ordinary rendering delay does not create a permanent dead end.
6. As a maintainer, I want unavailable evidence rejected before submission where possible, so that unsupported confirmations do not trap a new request.
7. As a player, I want healing, sentry and sleeping confirmations retained, so that the shared task module keeps its other behaviors.
8. As a maintainer, I want cancellation and lost-response tests, so that recovery does not replay an uncertain command.
9. As a player, I want the same run resumed from its confirmed checkpoint when process reload is needed, so that fixes preserve the game rather than start another world.
10. As a maintainer, I want T11 and real verification evidence recorded, so that later regression can reproduce this failure.

## Implementation Decisions

- Diagnose native event identity and operation representation before changing matching. Accept only evidence tied to the exact requested task and unit; do not weaken all task gates to generic movement or non-readiness.
- Keep existing ActionLifecycle and public task interfaces. Do not reset or silently release the current unknown request.
- Use an explicit same-run confirmed checkpoint for code-process recovery if needed.
- Keep exploration task start distinct from future path or exploration outcome.

## Testing Decisions

- Reuse the already confirmed public GameState / MCP seam with existing Lua-backed unit-task tests and their external native-event adapter. No new test seam is proposed.
- TDD against the observed native contract; test exact positive witness, wrong unit/world/turn/task, asynchronous delivery, lost reply, zero-submit refusal and no replay.
- Regression includes related task/readiness and pending-mutation gates; parent handles real-game reproduction and continuation.

## Out of Scope

Gameplay automation policy, background observers, resource or rule changes, broad runtime redesign, remote publishing.

## Further Notes

Original request #87 in this run evidence/tool-calls.jsonl, token 53879b1ea5c044b3ac638f76c274c80c, unit 196608, turn 11, seed -1896272000. Before (42,30), after (43,28), moves 0. Native task witness entered=true, returned=true, started absent, op=-1676983098. Unit IsAutomated=false and IsReadyToMove=false after current movement; unsupported GetAutomationType/GetOperationType absent. These facts alone do not prove lasting automation. Read-only evidence b02-native-task.txt retained. Last confirmed recovery checkpoint T2: gold_20261019_t002_b01_verified.
