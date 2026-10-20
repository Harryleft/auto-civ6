# R04 集成验证与操作文档

Status: resolved
Label: ready-for-agent
Dependencies: R01, R02, R03
Spec: ../spec.md

After three implementations merge, own only cross-feature public workflow tests, agent-playbook and acceptance documentation. Verify observe→native eligibility→religious action→fresh outcome while original pending gates remain; run affected public server/lifecycle regressions, document unexecuted native checklist. No broad refactor or live connection.

Read implement-spec and tdd. Worker must use an isolated worktree based on integration tip; confirm base, preserve others changes, no reset/rebase/amend. You are not alone in the codebase. Merge integration tip before reporting; commit only ticket work. Record actual RED→GREEN and commands/results. Parent owns spec/status and final review/closure.

## Resolution

Workflow implementation 4b49dc1; acceptance 014e2c8 and review-fix record 2b87c3a. Five same-engine public workflows including socket-blocked stdio metadata discovery. Final 15-suite result: 1308 passed in 35.68s on code1443382; compileall and diff checks pass.

Integration af85d28; independent Spec and Standards final rechecks both No material issues found. Delivery scope is offline implementation/validation; no FireTuner connection, game load, restart or turn advance. Native acceptance remains the explicit future checklist, not a completed claim. See ../../../acceptance/2026-10-20-religion-defense.md.
