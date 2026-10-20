# 宗教防御工具链

Label: ready-for-agent
Integration branch: codex/gold-terminal
Origin: 神级苏美尔 T141 宗教失败复盘；用户要求先补工具并验证，暂不重开下一局。

## Problem Statement

模型能看到宗教覆盖警报，但缺少同回合的逐城压力、可见宗教入侵者和实际反制资格。原生谴责已可执行，审判解锁与宗教购买也已存在；公开接口仍不能清除异教或进行和平神学战斗。摘要的原生统计容易被误读为本方城市数，且不同上下文事实可能冲突。单个动作成功不能证明整条防御链已生效。

## Solution

提供只读宗教防御查询，以及严格绑定原生目标与结果的清除异教、神学攻击动作。模型能从当前可见事实选择行动，并确认原生效果；信息未知或冲突时保持明确未知，不猜测胜利、剩余安全回合、权限来源或压力变化。

## User Stories

1. As a local game player, I want the current world identity, turn and local owner attached to religious defense facts, so that I do not use a prior turn as current evidence.
2. As a local game player, I want each owned city's native majority, followers and accumulated pressure, so that I can distinguish fragile religion from stable presence.
3. As a local game player, I want accumulated pressure distinguished from incoming pressure, so that action effects are not inferred from a per-turn rate.
4. As a local game player, I want visible foreign religious units even at peace, so that an empty military threat list does not hide missionaries.
5. As a local game player, I want the religion, full identity, location, charges and readable readiness of those units, so that I can choose an actual target.
6. As a local game player, I want hidden units and hidden foreign city details excluded, so that the interface does not reveal the map.
7. As a local game player, I want current native condemn eligibility and the corresponding own actor/target where known, so that war and congressional permission are not conflated.
8. As a local game player, I want permission source and expiry reported only when the engine provides sufficient evidence, so that old council reviews cannot be mistaken for active rights.
9. As a local game player, I want founded religion, native faith and inquisition state with their context, so that I can distinguish a purchase prerequisite from actual city defense.
10. As a local game player, I want city counts computed from owned cities separated from native religion-city statistics, so that a global statistic does not become an owned-city count.
11. As a local game player, I want cross-context or per-city/summary disagreements exposed, so that conflicting observations cannot yield a safety claim.
12. As a local game player, I want an eligible inquisitor to remove heresy through the native operation, so that foreign accumulated pressure can actually decrease.
13. As a local game player, I want the exact owned city and all relevant before/after foreign religious pressures bound to that action, so that another city or unrelated changes cannot confirm it.
14. As a local game player, I want the exact charge use or native final-charge consumption confirmed, so that disappearance alone cannot prove successful removal.
15. As a local game player, I want eligible religious units to attack an exact visible foreign religious unit at peace, so that native theological combat does not require a military war declaration.
16. As a local game player, I want exact combat witnesses and independently observed effects, so that a same-tile military unit or unrelated battle cannot satisfy confirmation.
17. As a local game player, I want native eligibility and immediate reachability to decide actions, so that unsupported movement, religious strength or ownership assumptions cannot submit an invalid order.
18. As a local game player, I want lost replies and repeated identical calls to confirm the original request only, so that religious charges and attacks cannot be spent twice.
19. As a local game player, I want known identity changes to stay invalid even beside unknown fields or after restoration, so that stale effects cannot clear pending state.
20. As a local game player, I want saves, end-turn and other mutations protected while a religious action is unknown, so that no incomplete action is silently bypassed.
21. As a project maintainer, I want offline public-interface tests executing actual generated Lua, so that native-boundary mistakes are caught without altering the finished game.
22. As a project maintainer, I want offline and native acceptance reported separately, so that passing adapters are not presented as a new real-game victory or action test.

## Implementation Decisions

- Keep the existing integration branch and local Markdown tracker. Three implementation tickets cover observation, remove-heresy and theological combat; a final dependent ticket covers integrated public workflows, documentation and acceptance.
- Introduce a read-only `get_religion_defense` public tool. It reports owned-city facts, visible religious-layer units, current native action qualifications where readable, faith and inquisition prerequisites, context/scope and explicit partial/unknown/conflicting evidence. It does not recommend or execute a fixed strategy.
- Use existing religious spread and victory queries for established facts without relabeling the native religion-city statistic as owned-city count. Owned counts must come from actual owned-city rows. Cross-context comparisons are read-only and scoped; unavailable Core evidence is unknown, never confirmation or a hardcoded cache fix.
- Use the exact native eligibility APIs and operation/command definitions found in shipped first-party UI and definitions. Do not infer permission from old review results or synthesize expiry. An unreadable permission source may remain unknown while an exact current native eligibility is known.
- Expose `unit_action(action='remove_heresy')` and `unit_action(action='religious_attack', target_x=..., target_y=...)`, with direct GameState counterparts. Preserve existing ordinary military attack behavior and existing spread/condemn/inquisition entrypoints.
- Bind full actor identity, owner, type, religion, position, original city/target and world/turn. Prefer a unique native eligible target; ambiguous religious-layer targets are refused before submission. Require visible target and fresh final native eligibility plus complete identity/scope recheck immediately before entry.
- Remove-heresy confirmation requires the original native operation witness, real charge decrement or exact native consuming event plus dual absence, same city identity, and an actual decrease in bound foreign accumulated pressures. Do not require an exact hardcoded 75% delta when modifiers apply, and do not claim own-pressure increases or city conversion without observing them.
- Religious combat uses the original exact actor/defender, native religious combat mode/parameters, native immediate path/eligibility and matching original combat events. It may occur at peace only when native religious eligibility allows; it never declares war or borrows military attack rules. A pending/queued move is not completed combat. If targets/actor are destroyed, match native events and original lookup absences without dereferencing consumed userdata or guessing death from visibility loss.
- Use the existing ActionLifecycle owner for both new mutations. Unknown results, cancellation and lost responses retain the original request; no repeat submission. World/turn/owner/actor/target mismatches latch invalid, including intermediate probes and valid mismatches beside unknown fields. Pure query cannot retire an action or close UI.
- Query unknowns must not change pending ownership. Do not introduce setters, synthetic native events, background scanning, autonomous defense, forced end-turn policy, new persistence or game-wide drivers.

## Testing Decisions

- Previously approved seams remain the public MCP / GameState boundary with executed Lua and native engine adapters; no new private-method test seam is introduced. User authorization and the earlier explicit seam approval persist.
- Work vertically: first one behavioral RED, then minimum implementation, then subsequent cases. Existing real-Lua spread, condemn, inquisition and scoped lifecycle tests are the prior art. Tests assert submitted native operation and externally reported outcome, not source strings or a copied state machine.
- Observation tests cover peace-time visible religious units, hidden-unit omission, own-city pressure stocks vs flow, unknown getter, same-turn context mismatch, per-city/native-summary conflict, native current permission true/false/unknown and absence of gameplay/UI mutations.
- Action tests cover successful surviving/final-charge removal, peace-time religious combat and relevant event orders, refusal/unknown native eligibility, same-plot other layers, missing/wrong events and effects, exact IDs/type/religion/city, pending move vs actual combat, lost submission/confirmation, native throw/cancellation, partial/unknown data, changed-then-restored scope/entity, and the existing mutation/save/end-turn gates.
- Run affected suites, compiler/import checks and diff checks, then independent Spec and Standards review. Broaden only for shared lifecycle/server changes or new failures.
- This delivery does not load, restart or advance Civ6. Current terminal evidence is preserved. Native action acceptance remains explicitly unexecuted; offline Lua adapters and source contracts do not substitute for it. Provide a bounded future native validation checklist without running a new game.

## Out of Scope

New game, save loading or replay, favorable-result recovery, any game mutation during this implementation, strategic automation, fixed religious thresholds in Runtime, historical outcome reassessment, speculative belief/faith/purchase rules, synthetic events, map reveals, direct game setters, publishing or pushing.

## Further Notes

The prior run ended at T141 with native Swedish religious victory. T139 was its last per-city religious snapshot. The post-terminal native religion-majority view conflicted with the terminal result; the exact final city/trigger is unknown. These facts motivate observability and action completeness, not a fabricated historical reproduction.
