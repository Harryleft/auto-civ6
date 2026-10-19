# B17：原生升级替换单位身份无法确认

Status: ready-for-agent

## Problem Statement

T75弓手655363在37,25执行一次原生升级，消耗165金币，旧单位被移除、新弩手1245193生成。工具却要求旧fullID仍存在且type变为47，#1375/#1379持续UNKNOWN，无法继续防守尼普尔。

## Solution

用原生升级事件确认原请求导致的单位替换，返回真实新fullID，同时保留原请求、身份、资源及单次提交约束。无法证明关联时继续unknown，不凭位置或类型相似选择单位。

## User Stories

1. As a player, I want native unit upgrades confirmed, so that upgraded defenders can act on later turns.
2. As a player, I want the new full unit ID reported, so that subsequent commands target the actual unit.
3. As a maintainer, I want the original full ID bound to its native command, so that an unrelated upgrade cannot confirm it.
4. As a maintainer, I want a complete removal/addition/upgrade chain, so that mere absence and proximity are insufficient.
5. As a maintainer, I want preexisting and ambiguous candidates rejected, so that another unit cannot be mistaken for the result.
6. As a player, I want exact native gold and strategic-resource costs confirmed, so that upgrades cannot be invented.
7. As a player, I want unknown repeats to confirm only, so that no second upgrade is submitted.
8. As a maintainer, I want world/turn/player/token mismatch retained, so that returning to an old value cannot revive invalid proof.
9. As a maintainer, I want delayed/lost replies and later invalidation handled, so that observer cleanup cannot manufacture success.
10. As a player, I want missing/malformed events kept unknown, so that a read failure never becomes authorization.
11. As a player, I want existing same-ID native upgrades compatible, so that supported engine behavior remains available.
12. As a player, I want same-run T75 replay and T76 validation, so that the repair demonstrably unblocks the original game.

## Implementation Decisions

- Keep public upgrade operation, ActionLifecycle and native Lua boundaries. Add the smallest request-bound alternative proof for actual entity replacement; no direct spawning, type/resource/HP/promotion setter or runtime gameplay policy.
- Capture baseline owned full IDs, original position and native target type before single submit. Original command enter/return and exact native upgrade chain must bind owner/full IDs/scope/token.
- Observed order: CommandStarted(old fullID, UPGRADE hash) → Removed(old fullID) → Added(new fullID, original x/y) → Upgraded(new fullID, native target type). Validate the new unit did not exist before, remains owned with exact fullID/type/location, and the old fullID no longer exists. Wrong/missing/ambiguous or pre-entry events cannot contribute proof. The engine emits a duplicate exact CommandStarted after Upgraded; it is not another submission and should not invalidate the proven chain.
- Preserve all existing resource baselines and same-ID completion contract. Known scope invalidation must constrain every completion branch; query-only unknown remains unknown and can later recover when no mismatch was observed. Keep necessary listeners after pure confirmation reads so lost replies cannot detach invalidation.
- Replacement result explicitly identifies old and new full IDs; follow-up gameplay is a separate model decision. Parent owns restoration and actual validation.

## Testing Decisions

Use approved public GameState/MCP/native Lua seams. Red→green the exact T75 chain; test single native submit, correct original/new identity and actual costs, wrong token/owner/world/turn/old/new/type/position, preexisting or multiple candidates, missing/malformed/out-of-order evidence, pre-entry/stale events, duplicate exact command event, old same-ID behavior and explicit refusal. Test lost response plus later invalidation and original confirm-only repeats. Parent explicitly restores pre-upgrade checkpoint, validates replacement/newID then T75→T76/save and later new-unit command. No new seam is needed.

## Out of Scope

Generic unit transformation redesign, deleting/replacing arbitrary units, automatic movement or attacks, changing upgrade costs/rewards or unrelated gameplay fixes.

## Further Notes

Native shipped UI consumes UnitUpgraded(playerID, unitID) to read the current resulting unit; actual third argument is targettype47. Full trace in evidence/b17-native-upgrade-events.txt: #1380 restored same-runT74, #1382–1387 replayed Education/Archer37,25/Builder38,25/disaster popup and T75; #1388 saved gold_20261019_t075_before_upgrade817921bytes; #1389 temporary pure event listeners; #1390 one public upgrade reproduces unknown. Tokena037186f085c49898679c3b509ce1ff7, CIVILIZATION_SUMERIA/-1896272000/T75/player0. Events: CommandStarted(0,655363,-750032448,0,0), Removed(0,655363), Added(0,1245193,37,25), Upgraded(0,1245193,47), duplicate identicalCommandStarted. Receipt beforetype28/target47, balance219.01953125/cost165, resource-1; actual gold54.01953125 and new1245193/type47/37,25/MP0/damage0. No explicit old→new mapping beyond this request-bound chain. Temporary listeners removed after pure diagnostic; parent must explicitly restore the confirmed T75 checkpoint before further gameplay.
