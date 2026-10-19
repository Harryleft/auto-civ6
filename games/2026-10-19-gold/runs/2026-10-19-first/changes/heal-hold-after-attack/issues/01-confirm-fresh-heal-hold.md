# B18-01 Confirm fresh native HEAL in exhausted HOLD

Status: resolved
Dependencies: none
Spec: ../spec.md

Implement the narrow native HEAL/HOLD task-start confirmation through existing public boundaries. Own necessary code/tests/ticket only; parent owns progress and live recovery. TDD red→green, affected checks, independent two-axis review; resolve after same-run T89→T90 checkpoint and actual later HP readback.

## Implementation and offline validation

- Fresh HEAL with the existing exact native entry/return/start witness can confirm native HOLD only when nonready and the current movement getter returns numeric zero. Existing identity/scope/eligibility gates and no-op branches remain in place; sleep and other task rules stay unchanged. The response remains `Native task started; completion not measured.`
- Red→green at public MCP/GameState/Lua 5.1: exact T89/1376259/HP39/MP0/HOLD previously stayed UNKNOWN, now confirms once with no HP/MP changes, including a lost submission reply. Unknown/nonzero/malformed movement and wrong/missing witness/readiness/identity remain pending without replay; HOLD does not become an already-healing no-op.
- A second red→green covers a native callback observing a changed seed during the new movement read: the final fresh-task scope check prevents success after that known invalidation.
- `tests/test_unit_tasks.py`: 152 passed, including ordinary HEAL/OPERATION, alert/sleep/automation and strict healthy/already-healing behavior. `compileall` and `git diff --check` passed.
- No implementer live calls or parent progress edits. Independent reviews and parent same-run T89 start→T90 checkpoint/later HP readback remain pending.

## Review and same-run validation

Implementation8cfe822; independent Spec and Standards reviews of c6f0bd7...8cfe822 found no material issues. Parent restored confirmed same-runT85 (#1615), replayed original decisions throughT89 (#1618–1655), saved pre-heal (#1656), refreshed only the clean MCP client and confirmed one HEAL start (#1659). T90 and checkpoint (#1660/#1664) confirmed. HP stayed39 after the attack turn; a separate explicit healT90 (#1668) led to T91 actualHP59 (#1693/#1695), checkpoint#1697. No synthetic recovery or duplicated native submit.
