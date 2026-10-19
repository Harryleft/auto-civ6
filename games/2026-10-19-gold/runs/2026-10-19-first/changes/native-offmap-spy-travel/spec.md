# B24：间谍跨城派遣的原生离图状态未确认

Status: ready-for-agent

## Problem Statement

T128首名间谍2752514由首都40,25派往加拿大渥太华18,13。#2384单次travel后UNKNOWN，#2387仅确认仍未知，get_spies误报No spies available。#2389原生receipt的精确owner/fullID/operation-1295211657(Index48)/cityOwner3/city65536/target18,13/世界T128及entered/returned/started均完整，但UnitManager及FindID均nil；#2390本方外交离图间谍原生表唯一行NameIndex37/NameLOC_CITIZEN_SUMERIA_MODERN_FEMALE_8/Level1/18,13/ReturnTurn132。工具要求已离图间谍仍有地图实体，无法确认真实旅行，阻塞同局。

## Solution

沿用单次spy assignment，把原生离图旅行作为真实派遣证据，严格关联原间谍和新离图记录，确认旅行开始及预计返回回合；查询呈现离图旅行事实，不能把它当消失、到达或任务成功。

## User Stories

1. As a player, I want native off-map spy travel confirmed, so that a real dispatch does not block the turn.
2. As a maintainer, I want original owned spy fullID/type/name/level and exact city owner/fullID/plot/location bound, so that a different spy or city cannot confirm.
3. As a player, I want exactly one native travel operation, so that retries never dispatch again.
4. As a maintainer, I want exact operation-start and original-unit removal plus a unique new native off-map travel record associated, so that absence alone is not success.
5. As a maintainer, I want ambiguous or unbindable native identity refused before mutation, so that missing off-map fullID cannot be replaced by guesswork.
6. As a player, I want a current travel record with target18,13 and ReturnTurn132 reported as in transit, so that I can wait for actual arrival.
7. As a maintainer, I want missing, malformed, duplicate, preexisting or unrelated off-map facts remain unknown or refuse before submission, so that cached or captured-spy data cannot confirm.
8. As a player, I want get_spies include native off-map travelling rows, so that the list does not falsely say no spies.
9. As a maintainer, I want native on-map mission confirmation/noop preserved and lost submit/confirmation/cancel/scope protections retained, so that this narrow travel fix does not weaken other missions.
10. As a maintainer, I want scope/identity changes latched and all pending mutation/save/end-turn gates preserved, so that restored old facts cannot confirm invalid intent.
11. As a player, I want same-run T128 travel, next turn/save and later native arrival observed, so that this specific blocker is verified.

## Implementation Decisions

- Extend existing spy assignment GameState/ActionLifecycle/nativeLua owner, no new generic lifecycle or background service. Existing public MCP/FakeConn/Lua5.1 seams approved.
- Native shipped EspionageOverview RefreshOperatives separately enumerates Players[local]:GetDiplomacy():GetNumSpiesOffMap and GetNthOffMapSpy(local,index); AddOffMapOperative uses Name,Level,XLocation,YLocation,ReturnTurn. These rows lack fullID. Require native identity that can actually be bound from the original spy plus a complete pre-dispatch roster snapshot, uniqueness and exact started/removed original fullID evidence. Validate facts before request; never correlate by target alone, row order, nearest unit or disappearance. If available native identity cannot prove the link, fail closed and gather evidence rather than fabricate an ID.
- Preserve native CanStartOperation true and one RequestOperation, exact original scope and city. Bind same identity before command entry. Confirmation accepts original on-map travel assignment as before, or strict new off-map travel with exact destination, finite future ReturnTurn, expected bound identity and original absence through both lookups. Final scope recheck and sticky invalidation remain mandatory; no synthetic events.
- Report SPY_ASSIGNMENT_STARTED with real native end turn, not arrival or mission outcome. get_spies reports own native off-map travelling facts and no actionable on-map fullID if none exists; do not expose or invent foreign hidden spies or control a row by NameIndex.
- Keep original request evidence through lost confirmation, retirement bounded by subsequent explicit request. New records cannot silently retarget old intent. Native on-map missions/noop/escape unrelated behavior preserved.

## Testing Decisions

Public MCP/GameState/nativeLua vertical red→green T128 case, exact off-map row/native Started+Removed and absent unit; missing/wrong events, existing/duplicate rows, mismatched name/level/target/turn, malformed facts/roster, unknown identities, wrong scope/city/actor, cancellation/lost submit/lost confirmation/retry-noresend and mutation/save/end-turn gates. Preserve on-map TRAVEL/noop/missions and owned-only query presentation. Avoid critical source-text-only assertions. Parent restores confirmed same T128 checkpoint after pure diagnosis (must not continue from closed unknown owner), prepares final pre-travel ready save with original fullID/name/level/roster observations; fresh final code executes once, checks off-map row, T129/save, and actual later arrival without forcing it.

## Out of Scope

Spy mission rewards/success/escape redesign, freeing captured spies, travel time changes, arbitrary unit creation/deletion, generic transport or whole-game automation, unrelated query cleanup.

## Further Notes

B23 concurrently fixes separate unit-effect spread pressure files; do not revert or overwrite that work. #2374 prior restore succeeded and #2375–83 replayed original independent T128 choices; #2384 introduced this travel unknown. Closed original owner for pure-only #2389–90; gameplay must resume only after explicit same-run checkpoint restore. Current confirmed checkpoint gold_20261019_t128_b22_verified/run e8b4d6a62dca49bbb256e8264beb18f2. Parent original-world command-start receipt already exact; no retry sent second operation.

## Observed native travel event order

Final-code first live #2425 submitted once, #2426 query correctly showed in-transit/ReturnTurn132; #2428 popup dismissed, #2429 confirm-only stillunknown. Scoped diagnostic #2431 records actual order: (1) UnitRemovedFromMap(0,2752514), (2) UnitOperationStarted(0,2752514,-1295211657,0). Receipt entered/returned/started=true, removed missing because handler incorrectly required started first. Both events belong to the entered single request. Accept these exact original events in native order (removal may precede start); still require both, same original scope and identities, dual lookup absence and unique new bound off-map record. Do not synthesize events or substitute absence for missing removal. Parent removed its temporary diagnostic listeners. Closed unknown owner was used only for pure diagnosis; explicit same-run post-spread checkpoint restore required before play.
