# B27-01 Launch native inquisition with bound consuming apostle proof

Status: pending-review
Dependencies: none
Spec: ../spec.md

Isolated worker owns necessary unit-action/GameState/native Lua/tests and this ticket. Parent owns real game/progress. Read implement-spec/tdd; existing approved public MCP/GameState/nativeLua seams. One explicit native launch, strict identity/religion/false preflag, exact Started+Removed either order, both absences and flagtrue; pending/confirm-only/lostreply/finalscope/sticky identity remain strict. Do not assume unavailable UnitOperationTypes enum, direct setters or a surviving/decremented-charge apostle. No scope expansion into inquisitor purchase/purge/evangelization. Preserve others' changes, merge integration before report. Merger plus independent Spec/Standards review and original T139/next-turn/save required before resolved.

## Implementation

- Added `unit_action(action='launch_inquisition')` and its guarded GameState method, reusing the existing unit-effect ActionLifecycle owner and a small inquisition witness mode. Native eligibility uses the actual `GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION.Hash` and four-argument `CanStartOperation(unit,hash,nil,{})`; no UnitOperationTypes fallback or resource/unit setters.
- Bind original fullID/type/owner/religion/coordinates and own founded religion, requiring an authoritative GameCore preflag exactly false in the original scope/token. Final native eligibility and actor/founder reads are followed by the complete InGame scope check, then the just-verified unit is submitted once.
- Confirmation independently reads InGame actor lookups/identities and GameCore founder/launch flag; known mismatches latch even alongside unknown peer fields or scope. Both exact Started and Removed events are required in either order, both lookups must be absent, and the same scoped Core receipt must observe false→true launch. Listeners and Core observations survive lost confirmation replies; a readable Core flag reversal also stays invalid.
- Primary shipped `Base/Assets/Gameplay/Data/Units.xml:755` identifies the apostle's LaunchInquisition capability; `Base/Assets/Text/en_US/Units_Text.xml:145–146` specifies launch consumes the apostle. Confirmation reports native launch and exact apostle consumption, with no guessed charge decrement, rewards or era score.

## Verification

- TDD red→green through public MCP/GameState and executed Lua: the exact T139 / unit3145737 / type4 / religion12 / 34,24 fixture initially returned Unknown action. It now confirms one native operation with both event orders, missing enum compatibility, native flag transition and dual absence. Native-consuming userdata can become unreadable after submission without any post-consumption unit access.
- Public regression includes false/unknown eligibility and preflag, foreign/wrong identity/religion, missing/wrong events, partial absence, unreadable flags, known-change/restore with unknown peers, final getter changed/unknown scope proving zero submission, final eligibility refusal, lost submit/confirmation, cancellation, native throw after effect, and mutation/save/end-turn gates. Existing activate/prophet/spread/condemn tests remain passing.
- `uv run --locked pytest -q tests/test_launch_inquisition.py tests/test_unit_effect_confirmation.py tests/test_condemn_heretic.py tests/test_spread_pressure.py tests/test_game_state.py tests/test_server.py`: **637 passed**. Compileall and `git diff --check` passed.
- Offline only; no game connection or push. Independent Spec/Standards review and parent original T139 launch, legal continuation, next-turn/checkpoint validation remain pending.

## Real context repair

- The first real request #2668 consumed the apostle and unlocked inquisitors, but #2674/2676 found InGame HasLaunchedInquisition still false; #2675 found the same native getter true in GameCore. No repeat operation was sent. Parent explicitly restored the original confirmed T139 ready checkpoint; worker remained offline.
- GameState now reads a scoped GameCore preflight before submission. It records only actual native false/own religion with token/fullID/world/turn/owner; no game flag or event is fabricated. The consuming command and exact event witness remain in InGame.
- The same ActionLifecycle owner has one optional GameCore confirmation query (default None preserves all ordinary single-context actions). Inquisition confirmation reads InGame witness → authoritative Core result → final InGame witness. Pure parsing requires both context proofs to match the exact token/fullID/owner/religion/civilization/seed/turn. Lost/unknown/contradictory/mismatched intermediate proof cannot retire the owner; no mutation is repeated.
- InGame no longer uses its cached launch flag. Its diagnostics distinguish missing receipt, changed/unknown identity or scope, missing events and incomplete dual absence; Core diagnostics distinguish missing receipt, scope/religion/flag changes, unknown data and a still-false flag.
- Public dual-Lua-runtime RED→GREEN reproduces the real cached false/Core true split. Coverage includes Core true/unknown pre-state zero submission, Core/final-InGame reply loss and cancellation, known Core changes with unknown peers, late Core/world changes, missing original receipts, mismatched/duplicate/unknown proof, and unknown-Core recovery while the UI cache remains false.
- Final related validation: `uv run --locked pytest -q tests/test_launch_inquisition.py tests/test_unit_effect_confirmation.py tests/test_condemn_heretic.py tests/test_spread_pressure.py tests/test_game_state.py tests/test_server.py tests/test_action_confirmation.py tests/test_action_scope.py` — **787 passed**. Compileall and diff check passed. Dual review and final parent native T139/next-turn/save checks remain pending.

## Cross-context review repair

- Both review axes reproduced a first InGame ACTION_IDENTITY seed mismatch that was overwritten by the final response. Six public real-Lua cases first incorrectly released the owner, including an unknown owner field and a later lost/cancelled Core response. ActionLifecycle now consumes every readable scope field in each received InGame probe before another await or response replacement, using the existing sticky scope observer. This does not parse completion or retire the owner from the first witness; default None actions retain their original path.
- Added unknown-first-probe recovery and final-probe mismatch/unknown-peer cases. The same eight affected test files now report **797 passed**; compileall and diff check passed. Worker performed no game action; review recheck and parent final native checks remain pending.
