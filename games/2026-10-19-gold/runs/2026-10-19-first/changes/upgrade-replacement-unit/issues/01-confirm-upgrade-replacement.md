# B17-01 Confirm native upgrade replacement identity

Status: resolved
Dependencies: none
Spec: ../spec.md

Implement the minimal native request-bound upgrade replacement witness, preserving original costs/scope/once-only confirmation and same-ID compatibility. Worker owns necessary code/tests/ticket; parent owns live diagnosis/recovery and progress. TDD at approved public boundary, affected checks, independent Spec/Standards review; resolve only after original same-run T75→T76 checkpoint and actual new-unit follow-up.

## Implementation and offline validation

- Native `UnitFlagManager.lua:1094` names Added's full unit ID and position; `:1810` reads the resulting unit from UnitUpgraded. The request-bound Started→Removed→Added→Upgraded sequence and duplicate exact Started come from parent evidence `b17-native-upgrade-events.txt`.
- Capture owned full IDs and original coordinates before entry. Four bounded listeners bind the original command/full ID, then one fresh added ID at the source position and its exact native target type. Pre-entry, missing, malformed, wrong-order or ambiguous events do not prove replacement. An identical repeated Started is idempotent.
- Replacement confirmation requires the exact old ID absent, the exact new owned ID/type/position present, exclusion from the owned-ID baseline, and the original gold/strategic-resource deductions. The response identifies both IDs. The original same-ID branch remains available independently.
- Known scope history uses the shared `invalid` flag before both branches and after native reads; chain ambiguity/unknown evidence stays separate. Pure confirmation retains listeners, so a lost reply cannot detach later scope/candidate invalidation. Only the next explicit upgrade removes prior owned listeners.
- Red→green through public MCP/GameState/Lua 5.1: the T75 trace changed from UNKNOWN to `UPGRADED|unit:1245193|type:28->47,old_unit:655363,replacement:confirmed`, with one submission and balance 54.01953125. Focused replacement, existing transforms and unit-task tests: 273 passed. `compileall` and `git diff --check` passed.
- Implementation b10da9b integrated; independent Spec and Standards reviews reported no material findings. Parent #1399 confirmed the original T75 upgrade to new1245193 with old655363 explicitly identified; #1402 advanced T76, #1403 read the actual crossbow, #1409 successfully commanded the new fullID to35,25. #1413 confirmed818002byte checkpoint gold_20261019_t076_b17_verified. The original diagnostic world had been explicitly restored via#1391 before this validation; no silent reconciliation or duplicate upgrade submission.
