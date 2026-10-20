# R03 原生和平神学战斗

Status: ready-for-agent
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Own religious-combat action module, focused test_religious_attack.py and minimal public unit_action/GameState wiring. Exact religious-layer target and native eligibility/path/operation/combat proof at peace. Reuse lifecycle; do not modify military attack implementation, observation or remove-heresy builders/tests. No live connection.

Read implement-spec and tdd. Worker must use an isolated worktree based on integration tip; confirm base, preserve others changes, no reset/rebase/amend. You are not alone in the codebase. Merge integration tip before reporting; commit only ticket work. Record actual RED→GREEN and commands/results. Parent owns spec/status and final review/closure.
