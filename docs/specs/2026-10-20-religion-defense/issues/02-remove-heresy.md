# R02 原生清除异教

Status: resolved
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Own remove-heresy action module, focused test_remove_heresy.py and minimum public unit_action/GameState wiring. Reuse original lifecycle; exact unit/city/foreign pressure and charge/consumption proof, strict pending/lost-reply/scope gates. Do not edit observation or theological action module/tests. No live connection.

Read implement-spec and tdd. Worker must use an isolated worktree based on integration tip; confirm base, preserve others changes, no reset/rebase/amend. You are not alone in the codebase. Merge integration tip before reporting; commit only ticket work. Record actual RED→GREEN and commands/results. Parent owns spec/status and final review/closure.

## Resolution

Implementation bd22a24; 70 focused actual-Lua cases. Single no-parameter native operation, exact original city/foreign pressure/charge proof and pending protection. Real native target shape and consuming-event order remain explicitly unexecuted.

Integration af85d28; independent Spec and Standards final rechecks both No material issues found. Delivery scope is offline implementation/validation; no FireTuner connection, game load, restart or turn advance. Native acceptance remains the explicit future checklist, not a completed claim. See ../../../acceptance/2026-10-20-religion-defense.md.
