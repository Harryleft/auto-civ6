# R01 宗教防御只读查询

Status: ready-for-agent
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Native observation/reporting only: new religion_defense query/model/narration and its public tool/GameState adapter, focused tests. Own observation implementation, test_religion_defense.py and minimal relevant public-entry exports; do not edit action builders or action tests. Include context/scope, owned city counts/pressure, visible religious layer units, native current condemn eligibility, honest permission-source/expiry unknown and inquisition/faith reads. Clarify existing ambiguous religion-city statistic. No gameplay or live connection.

Read implement-spec and tdd. Worker must use an isolated worktree based on integration tip; confirm base, preserve others changes, no reset/rebase/amend. You are not alone in the codebase. Merge integration tip before reporting; commit only ticket work. Record actual RED→GREEN and commands/results. Parent owns spec/status and final review/closure.
