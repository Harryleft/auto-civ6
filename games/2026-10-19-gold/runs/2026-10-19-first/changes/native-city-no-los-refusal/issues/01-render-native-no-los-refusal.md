# B26-01 Render native city NO_LOS refusal without submitting a shot

Status: resolved
Dependencies: none
Spec: ../spec.md

Worker owns necessary city Lua generator/regression tests/this ticket in isolated worktree. Use implement-spec/tdd, existing approved public MCP/GameState/native Lua seams; preserve other changes and do not touch live/progress. Root supplies actual T138 native refusal/restore proof. Implement only the scoped pre-entry error rendering fix; verify no command/listener/new receipt, clear public refusal/no pending, and existing known/unknown action behavior. Merge integration before report. Independent merger and dual review required; root resolves after same-run native refusal/continuation/nextturn/save.

## Final verification

Implemented e7c40b3 (single production expression fix, actual Lua 5.1 and public GameState regressions). Relevant110 checks/compile/diff pass; independent Spec/Standards no material issues. Parent#2629 explicit same-run T137 recovery61.453s,#2630–32 full world/units/spy checked;#2633 sameT138 events,#2634 sameDebater/#2635 sameFieldkill,#2636 exactoriginalenemy dualabsent and native Bombard refusalfalse,#2637 ready checkpoint929991bytes. Final clean refresh without world reload#2638T138. #2639 before qnil,target6422531/damage0/34,20;#2640 original public city_action returns Error:NO_LOS actual34,20/from35,22;#2641 sameqnil/targetunchanged, no new native witness. #2643 explicit existing builder-queue cancellation/#2644 separate normal movement succeeds,#2647 saveT138929929bytes,#2648T139 confirmed/#2649 save928834bytes. No historical pending owner reconstructed or retrospectively released.
