# B21：原生可创教的预言家被聚合标记拦截

Status: ready-for-agent

## Problem Statement

T125招募琐罗亚斯德114，预言家2621451已到首都圣地39,26，MP2.25、createdReligion=-1、beliefsEarned=0，原生CanStartOperation(FOUND_RELIGION,当前坐标,true)=true。unit_action activate #2249却因玩家HasReligiousFoundingUnit=false返回FOUNDING_NOT_AVAILABLE，零提交。最后宗教名额尚在，阻止建立宗教以应对瑞典宗教胜利风险。

## Solution

依照绑定当前原始预言家与圣地坐标的原生FOUND_RELIGION资格决定可否请求；聚合玩家标记的false不能覆盖原生具体操作true。保持单次提交、同原始世界/单位/回合的开始及就绪结果确认。

## User Stories

1. As a player, I want the native eligible original prophet to activate, so that I can prepare religion founding.
2. As a player, I want false aggregate founding-unit flags not to veto native operation eligibility, so that valid actions are not blocked.
3. As a maintainer, I want the exact original prophet, individual, owner and coordinates bound, so that unrelated units cannot authorize activation.
4. As a player, I want zero native action charges supported for prophets, so that the actual prophet data works.
5. As a maintainer, I want native false or unavailable FOUND_RELIGION eligibility refused before writes, so that uncertainty cannot submit.
6. As a player, I want movement and uncreated-religion conditions preserved, so that the repair does not bypass engine rules.
7. As a maintainer, I want one native operation per request, so that retries cannot consume or activate twice.
8. As a player, I want activation readiness distinct from religion/selected beliefs, so that completion is not invented.
9. As a maintainer, I want started-event, removal or original-unit facts and earned-belief evidence retained, so that unrelated changes cannot confirm.
10. As a maintainer, I want wrong owner, unit, individual, world or turn to retain unknown, so that stale readbacks cannot confirm.
11. As a player, I want refusal and lost-response behavior retained, so that ordinary gameplay remains protected.
12. As a player, I want original T125 activation and actual readiness verified before separately choosing religion and beliefs, so that this blocker is reproduced in the original run.

## Implementation Decisions

- Use existing public MCP/GameState unit-effect/native Lua seams, already authorized for this run. No new test interface or user approval needed.
- Remove the aggregate HasReligiousFoundingUnit==true precondition as the authority over exact native operation eligibility. Keep original uncreated religion, valid earned-belief baseline, native zero-charge prophet specialization, exact operation mapping, positive movement and native CanStartOperation true before submission.
- Preserve original ActionLifecycle, single request/confirm-only/lost-response scope checks and existing effect confirmation. Do not relax post-operation result evidence without live evidence; a subsequent result mismatch must be diagnosed separately.
- No direct religion/belief/player-flag/unit setter, synthetic event or manual RequestOperation through diagnostic Lua. Runtime owns actual action.

## Testing Decisions

At approved public MCP/GameState/FakeConn/Lua5.1 seam, red→green original T125 false aggregate flag + native eligible zero-charge prophet submits once and confirms founding readiness only. Cover native false/unknown/error zero-submit, wrong original identities/scopes/events, lost response/retry no resend and preserve existing non-prophet activation/spread behavior. Adapt obsolete tests that encoded the incorrect aggregate veto; no source-text-only assertion. Parent same-run T125 activation/readiness and later religion choice/turn/save verify real behavior. Do not guarantee unobserved future founding results.

## Out of Scope

Generic religion/GP refactors, unrelated belief-query filtering, bypassing native unit operation, forcing a religion or belief, unrelated operations, broad lifecycle changes.

## Further Notes

#2246 pre-prophet T125 confirmed checkpoint892471bytes; #2247 native recruit114 costs40→unit2621451; #2248 move39,26; #2249 zero-submitFOUNDING_NOT_AVAILABLE. #2251 original unit/coords/MP2.25, created=-1, flag=false, beliefs0, native operationHash-953161477 and CanStartOperation=true. No pending action/proposal. Parent completes independent T125 orders and saves ready checkpoint before clean code refresh, no world reload needed. Same run e8b4d6a62dca49bbb256e8264beb18f2, seed-1896272000.

## T125 live post-state evidence and bounded extension

24bdc8d removes the false preflight veto; 113 tests and dual reviews pass. #2269 original operation now enters once and removes prophet but existing post-check still reports FOUNDING_READINESS_UNCONFIRMED; #2273 confirm-only after closing TechCivicCompletedPopup stays unknown. No religion-choice request sent. Original pending owner clean-closed solely for pure diagnosis; no gameplay may resume before explicit same-run T125ready restore.

#2275 pure native receipt: same token c0396d399c75419ea5282ee145dbefdc, owner0/unit2621451/individual114/class4/type13/world/seed/turn125/x39,y26/FOUND_RELIGIONhash-953161477, entered=true/returned=true/started=true/removed=true; original fullID absent from both GetUnit and playerUnits.FindID. Player religion now created=-1, earnedBeliefs=2, HasReligiousFoundingUnit=true (before false). This directly contradicts old post-check requiring false. Extend this same blocker to require the observed positive founding-readiness flag after exact operation/effect evidence, while keeping earned increase, original uncreated identity, original unit/removal/event/scope and no-resend rules. False, unknown or unavailable post readiness remains unknown. Do not accept either boolean indiscriminately or infer religion already created. Add public native false→true/0→2 fixture red→green and preserve post-false/unknown mismatch regressions. Parent explicitly restores gold_20261019_t125_b21_ready893389bytes before replay of this one activation using final code.
