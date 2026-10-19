# B27-01 Launch native inquisition with bound consuming apostle proof

Status: pending-review
Dependencies: none
Spec: ../spec.md

Isolated worker owns necessary unit-action/GameState/native Lua/tests and this ticket. Parent owns real game/progress. Read implement-spec/tdd; existing approved public MCP/GameState/nativeLua seams. One explicit native launch, strict identity/religion/false preflag, exact Started+Removed either order, both absences and flagtrue; pending/confirm-only/lostreply/finalscope/sticky identity remain strict. Do not assume unavailable UnitOperationTypes enum, direct setters or a surviving/decremented-charge apostle. No scope expansion into inquisitor purchase/purge/evangelization. Preserve others' changes, merge integration before report. Merger plus independent Spec/Standards review and original T139/next-turn/save required before resolved.

## Implementation

- Added `unit_action(action='launch_inquisition')` and its guarded GameState method, reusing the existing unit-effect ActionLifecycle owner and a small inquisition witness mode. Native eligibility uses the actual `GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION.Hash` and four-argument `CanStartOperation(unit,hash,nil,{})`; no UnitOperationTypes fallback or resource/unit setters.
- Bind original fullID/type/owner/religion/coordinates and own founded religion, requiring the native preflag exactly false. Final eligibility and identity/flag reads are followed by the complete scope check, then the just-verified unit is submitted once.
- Confirmation independently reads both native actor lookups, all readable actor identities, own founder and launch flag; known mismatches latch even alongside unknown peer fields or scope. Both exact Started and Removed events are required in either order, both lookups must be absent, and false→true launch must be observed. Listeners survive lost confirmation replies; a readable flag reversal also stays invalid.
- Primary shipped `Base/Assets/Gameplay/Data/Units.xml:755` identifies the apostle's LaunchInquisition capability; `Base/Assets/Text/en_US/Units_Text.xml:145–146` specifies launch consumes the apostle. Confirmation reports native launch and exact apostle consumption, with no guessed charge decrement, rewards or era score.

## Verification

- TDD red→green through public MCP/GameState and executed Lua: the exact T139 / unit3145737 / type4 / religion12 / 34,24 fixture initially returned Unknown action. It now confirms one native operation with both event orders, missing enum compatibility, native flag transition and dual absence. Native-consuming userdata can become unreadable after submission without any post-consumption unit access.
- Public regression includes false/unknown eligibility and preflag, foreign/wrong identity/religion, missing/wrong events, partial absence, unreadable flags, known-change/restore with unknown peers, final getter changed/unknown scope proving zero submission, final eligibility refusal, lost submit/confirmation, cancellation, native throw after effect, and mutation/save/end-turn gates. Existing activate/prophet/spread/condemn tests remain passing.
- `uv run --locked pytest -q tests/test_launch_inquisition.py tests/test_unit_effect_confirmation.py tests/test_condemn_heretic.py tests/test_spread_pressure.py tests/test_game_state.py tests/test_server.py`: **637 passed**. Compileall and `git diff --check` passed.
- Offline only; no game connection or push. Independent Spec/Standards review and parent original T139 launch, legal continuation, next-turn/checkpoint validation remain pending.
