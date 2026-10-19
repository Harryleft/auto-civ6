# B12：耗尽移动力后的警戒开始确认

Status: resolved

## Problem Statement

T57弓箭手786437移动到(41,27)耗尽移动力，#1014警戒请求及#1017/#1019原请求确认均OUTCOME_UNKNOWN。只读原生记录证明本次entered/returned/started均true，ready=false，activity=HOLD，现有逻辑只允许SENTRY/OPERATION。

## Solution

精确本次警戒开始后，允许这个引擎HOLD状态确认任务已接纳；保留真实就绪、作用域和事件见证，不把接纳等同持续警戒。

## User Stories

1. As a player, I want native accepted alert confirmed after movement, so that the same game can continue.
2. As a player, I want HOLD interpreted from real native evidence, so that engine behavior is represented accurately.
3. As a player, I want task start distinguished from sustained sentry, so that success is not overstated.
4. As a maintainer, I want exact Started/Added identity retained, so that unrelated operations stay unknown.
5. As a maintainer, I want owner/unit/world/turn/token retained, so that stale actions stay unknown.
6. As a player, I want repeated confirmation never to resend, so that one request remains one operation.
7. As a maintainer, I want missing or malformed readiness/activity left unknown, so that missing evidence is not success.
8. As a maintainer, I want HOLD insufficient for alert no-op, so that unrelated standing tasks are not misclassified.
9. As a maintainer, I want heal/sleep/automate unchanged, so that this repair remains scoped.
10. As a player, I want T57 faithfully restored and reproduced, so that the same run and prior choices remain.
11. As a player, I want T57→T58 confirmed after repair, so that the blocker has a live validation result.

## Implementation Decisions

- Extend fresh alert confirmation only to numeric native ACTIVITY_HOLD after entered/returned/exact started and boolean ready=false. Preserve existing SENTRY/OPERATION acceptance, strict no-op, one-submit and action ownership contracts.
- Never call activity setters, synthesize events or change resources. No blanket task acceptance based on not-ready alone.
- Parent owns live game. Closing unresolved MCP for diagnosis requires explicit T56 same-run checkpoint restoration, faithful replay, then T57 save before repaired alert.

## Testing Decisions

Use already approved public GameState/MCP and native Lua fixture seams, red→green. Verify exact fresh alert HOLD succeeds, repeated confirmation does not resubmit; wrong/missing start, stale scope, ready/unreadable values, unrelated activity and no-op HOLD remain unknown. Preserve heal/sleep/automate behavior. Parent validates same-run T57 request and T58 progression.

## Out of Scope

Generic task relaxation, guaranteed future sentry, game strategy automation, direct state writes or unrelated fixes.

## Further Notes

Evidence b12-native-alert.txt: token875307fbbf9b4e63b1662ca88997b018, unit786437, expectedSENTRY=-793848223, actualHOLD=75236491, operationHash=-448283625/Index54, entered/returned/started=true, ready=false, moves0, CIVILIZATION_SUMERIA/-1896272000/T57/player0. Original confirmation #1017/#1019, popup cleanup#1018 no effect. Restore gold_20261019_t056_b11_verified (#990), run e8b4d6a62dca49bbb256e8264beb18f2.

Validation: 857865c／集成26c3467；372相关测试、compileall/diff-check通过，Spec与Standards独立审查无实质发现。#1020恢复T56，#1024–1041忠实重放，#1042保存T57警戒前检查点；#1044原生警戒开始确认成功。随后#1046推进被战车新本地待办阻塞，#1049单位处理被pending门禁拒绝，#1051确认保留原请求；T58联验转B13继续，未声称已推进。
