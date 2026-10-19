# B18：攻击后休整停在原生HOLD无法确认

Status: ready-for-agent

## Problem Statement

T89驻军弩手1376259在35,22射击后请求heal。原生HEAL精确started/entered/returned均true，单位HP39/MP0/nonready，活动HOLD75236491；工具仅兼容HEAL和OPERATION，#1609/#1612/#1614始终unknown，阻塞游戏。

## Solution

在原任务身份、作用域和精确开始见证下，确认本次HEAL已开始。将观察到的HOLD/0MP与未来回血分开；继续依靠后续回合实际HP验证恢复，不从静止或生命值推断任务成功。

## User Stories

1. As a player, I want a native post-attack heal request recognized, so that the unit does not lock the game.
2. As a player, I want task start separated from health recovery, so that future results are not invented.
3. As a maintainer, I want original token/fullID/owner/world/turn binding, so that unrelated HOLD cannot confirm this request.
4. As a maintainer, I want fresh exact HEAL entry/return/start evidence, so that activity alone is insufficient.
5. As a maintainer, I want nonready/HOLD/zero-move facts checked, so that broader unrelated postures remain unsupported.
6. As a player, I want repeated unknown calls to confirm only, so that no operation is sent twice.
7. As a maintainer, I want missing or malformed data kept unknown, so that read failures cannot authorize follow-up.
8. As a maintainer, I want scope changes to remain invalid, so that restored values do not revive mismatched evidence.
9. As a player, I want already-healing and healthy no-op behavior preserved, so that existing standing orders remain compatible.
10. As a maintainer, I want alert/sleep/automation boundaries preserved, so that this correction does not loosen every task.
11. As a player, I want the same original T89 decisions replayed, so that combat and trading outcomes are preserved.
12. As a player, I want a later-turn checkpoint and actual recovery readback, so that the repaired task demonstrably unblocks play.

## Implementation Decisions

- Prefer the existing public task lifecycle and native Lua confirmation. Add only the observed fresh HEAL/HOLD/nonready/zero-move alternative; keep strict native eligibility and request identity.
- No HP/MP/activity setter, synthetic event, automatic turn advance or retry. Do not label HP recovered until actually read after later turns.
- Preserve no-op strictness and other task types. Missing/invalid current activity, movement, readiness or witness continues unknown.
- Parent owns live diagnosis/recovery. After closing the unresolved owner for pure diagnosis, restore the confirmed same-run T85 checkpoint and replay the exact original decisions through T89. Save immediately before the heal for final validation; do not continue the unknown diagnostic world.

## Testing Decisions

Use already approved public GameState/MCP/native Lua seams, following existing native task tests. Red→green the exact T89 started HEAL/HP39/nonready/HOLD/MP0 scenario. Cover missing/stale/wrong token/fullID/owner/operation/scope evidence, absent entry/return/start, unknown/nonzero moves and readiness, strict no-ops, ordinary HEAL/OPERATION and alert/sleep compatibility. Retain confirm-only/lost-response behavior and one native submit. Parent verifies this same T89 start, subsequent T90/save and later observed HP recovery, allowing a separate new heal decision if native HOLD wakes next turn.

## Out of Scope

Generic posture redesign, inferring future healing, changing combat/turn rules, all-task relaxation or unrelated lifecycle refactors.

## Further Notes

Evidence: b18-native-heal-hold.txt after #1609 original, #1610/1611 pure reads, #1612 confirm-only, #1613 popup close, #1614 confirm-only. Exact tokenf9ab157695c2482d81b583972417612d, requested/full1376259, owner0, CIVILIZATION_SUMERIA/-1896272000/T89, opHEAL2126026491/Index13, entered/returned/startedtrue, current35,22/HP39/MP0/readyfalse/HOLD75236491, busy/processingfalse. Original expectedHEAL-1298237662, OPERATION1225574625. B12 previously established fresh ALERT/HOLD, but this is HEAL. Latest confirmed same-run recovery gold_20261019_t085_defense_and_courser831943bytes. T89 earlier actions and RNG outcomes remain in the original call log for faithful replay.
