# B20：研究同盟被错误市政前置拦截

Status: ready-for-agent

## Problem Statement

T108拟与宣布友好的巴西签研究同盟，以提升两条商路科研并触发外交部门提升。get_trade_options报告须外交部门，form_alliance #1996零提交NO_CIVIC。实际双方行政部门已完成、本方外交部门未完成；当前GameInfo研究同盟行动双方PrereqCivic均为CIVIC_CIVIL_SERVICE，原生IsDiplomaticActionValid对研究同盟/巴西=true。静态Civilopedia也说明双方行政部门。错误硬编码阻止合法提案并形成先研究本应通过结盟提升的市政的循环。

## Solution

按当前原生具体同盟行动资格决定可否提案，查询和执行使用同一规则来源；保留友谊、战争、单一同盟、原生拒绝和未知边界，不从科技或友好状态单独推断可以成交。

## User Stories

1. As a player, I want a native eligible alliance proposal allowed, so that I can ally before Diplomatic Service.
2. As a player, I want the exact selected alliance type checked, so that eligibility for another type does not authorize mine.
3. As a maintainer, I want current native action data and validation used, so that a hardcoded civic cannot override the engine.
4. As a player, I want query eligibility consistent with execution, so that legal choices are discoverable.
5. As a maintainer, I want missing, malformed or unreadable eligibility to refuse before any deal/session mutation, so that uncertainty cannot submit.
6. As a player, I want native refusal preserved, so that friendship alone cannot force an alliance.
7. As a maintainer, I want existing war/friendship/already-allied and pending-deal boundaries preserved, so that this fix does not bypass relationships.
8. As a player, I want proposal, AI acceptance and actual bilateral alliance confirmation separate, so that willingness is not invented.
9. As a maintainer, I want existing one-sequence/confirm-only/lost-response rules preserved, so that repeated calls cannot open another proposal.
10. As a maintainer, I want mismatched world/owner/target/type evidence retained as unknown, so that stale readbacks cannot confirm.
11. As a player, I want documentation to state the actual native eligibility contract, so that it does not repeat the false prerequisite.
12. As a player, I want same-run T108 verification before researching Diplomatic Service, so that the repaired prerequisite is actually tested.

## Implementation Decisions

- Prefer existing relationship MCP/GameState/native Lua seams and source-owned native action eligibility. Resolve the requested alliance type to its actual native diplomatic action, verify that binding, then require native IsDiplomaticActionValid true. Read errors/unknown/invalid action fail closed before working-deal mutation. No manually adding civics or forcing diplomacy.
- Replace the wrong Diplomatic Service gate in alliance execution and related public eligibility queries. Shared eligibility interpretation should avoid diverging copies. Preserve existing query schemas where practical; a generic eligibility bool may represent any supported currently valid alliance, while execution always validates the exact requested type. Do not present unknown as an affirmative eligibility claim.
- Keep existing relationship lifecycle and native proposal/acceptance witnesses; no generic lifecycle rewrite, deal-duration redesign or unrelated diplomacy fixes. Current no-op exact alliance behavior stays.
- Update tool/query wording that asserted the wrong hardcoded prerequisite. Existing native Civil Service requirements are evidence for this world, not a replacement global hardcoded gate.

## Testing Decisions

Use already approved public MCP/GameState/FakeConn/Lua5.1 seams. Red→green T108 friendship/CivilService=true/DiplomaticService=false/nativeResearchValid=true. Test exact typed action binding, false/unknown/error/missing or wrong action/other-type-only eligibility and zero native mutations; preserve refusal/war/friendship/already-allied/unknown pending-deal, confirm-only/lost-response single proposal sequence and actual bilateral type confirmation. Query and execution agreement through existing tests; no source-text-only assertions for native eligibility. Parent verifies same original T108 proposal before DiplomaticService completion, actual alliance or factual native AI refusal as observed, then subsequent turn/checkpoint. AI refusal is not itself a tool defect; no acceptance guarantee.

## Out of Scope

Forcing AI acceptance, invented alliance benefits, artificial civic/relationship changes, generic diplomacy refactors, bypassing the standard native proposal sequence, unrelated terms/duration changes.

## Further Notes

#1990 false query; #1994 knowledgeMECHANIC_DIPLOMACY_ALLIANCES rule/Civilopedia; #1995 live GameInfo typed research actionHash430927667, bothPrereqCivic=CIVIC_CIVIL_SERVICE, localHasCivil=true/HasDiplo=false; #1996 native-toolNO_CIVIC zero submit; #1997 native IsDiplomaticActionValid(actionType,2,true)=true, targetCivil=true, friendstate1. Same Sumeria/seed-1896272000/run e8b4d6a62dca49bbb256e8264beb18f2. No pending alliance proposal. Parent completes independent T108 orders and saves before clean MCP refresh; no world replay needed.
