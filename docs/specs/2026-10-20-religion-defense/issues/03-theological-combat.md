# R03 原生和平神学战斗

Status: resolved
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Own religious-combat action module, focused test_religious_attack.py and minimal public unit_action/GameState wiring. Exact religious-layer target and native eligibility/path/operation/combat proof at peace. Reuse lifecycle; do not modify military attack implementation, observation or remove-heresy builders/tests. No live connection.

Read implement-spec and tdd. Worker must use an isolated worktree based on integration tip; confirm base, preserve others changes, no reset/rebase/amend. You are not alone in the codebase. Merge integration tip before reporting; commit only ticket work. Record actual RED→GREEN and commands/results. Parent owns spec/status and final review/closure.

## Resolution

Implementation b83450c; native nil-return contract correction 3e78595; Spec P1 coordinate-latch fix 1443382. 99 focused actual-Lua cases. Original public reproducer now remains unknown with invalid=True/pending retained and one submission after restoration.

Integration af85d28; independent Spec and Standards final rechecks both No material issues found. Delivery scope is offline implementation/validation; no FireTuner connection, game load, restart or turn advance. Native acceptance remains the explicit future checklist, not a completed claim. See ../../../acceptance/2026-10-20-religion-defense.md.
