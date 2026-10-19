# B22-01 Add native condemn_heretic unit action

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Worker owns necessary implementation/tests/ticket in isolated worktree; parent owns live/progress. Read implement-spec/tdd; use approved public MCP/GameState/FakeConn/nativeLua seams. Red→green exact eligible T127 action, fail-closed target/eligibility and original-scope single-command confirmation. Preserve adjacent unit effects and gates. Merge current integration before reporting commit/tests; independent merger and Spec/Standards review then parent same-run live/next-turn save before resolution. No game operations by worker.

## Offline implementation

- Added `unit_action condemn_heretic` and the guarded GameState entry through the existing unit-effect owner. Requires exact current native command metadata and `CanStartCommand(actor, hash, false, true)==true`, then submits one parameterless `RequestCommand`; no war-only override or direct effects.
- The visible actor tile is read with shipped WorldInput.lua:453 `Units.GetUnitsInPlotLayerID(x,y,MapLayers.ANY)` (parent #2326 confirms the native table includes both original Musket and Apostle). Only a unique foreign `TrackReligion` unit with positive native religious strength/religion is bound. Rechecks actor and target after eligibility; ambiguity, unavailable facts and native refusal produce zero commands. `UnitCommands.xml` names the row field `CommandType`; UnitPanel.lua:2452/2513 defines the command-start signature and parameterless native dispatch.
- Confirmation requires original token/scope, entered/returned, exact command-start owner/full actor/hash, original actor type/location, exact original target-removal owner/fullID, and absence in both native target lookups. Known observed identity or scope changes latch invalid; temporary unknown facts do not. Same-call retries cannot retarget a new occupant. Result reports only frozen target removal and leaves rewards unmeasured.
- T127 public MCP/native-Lua reproduction was red with Unknown action, then green. Additional red→green cases cover target change during native eligibility, observed target/actor identity restoration, and unreadable actor type. Regression tests cover native false/unknown/errors/bad metadata, target visibility/ambiguity, missing or mismatched events/effects, changed scope, lost/cancelled replies, no resend, and pending mutation/save/turn gates.
- `uv run --locked pytest -q tests/test_condemn_heretic.py tests/test_unit_effect_confirmation.py tests/test_server.py` — **363 passed**. `uv run --locked python -m compileall -q src tests/test_condemn_heretic.py` and `git diff --check` passed.
- Implementer did not access the live game. Independent review and parent original T127 action/next-turn/checkpoint validation remain pending.

## Review corrections

- Confirmed replies can be lost before the Python owner retires. Condemn confirmation now retains its two bounded native witnesses, preserving sticky scope invalidation after such a lost reply; the next actual unit-effect request replaces/cleans those listeners. Other unit-effect cleanup behavior is unchanged. Public red→green regressions lose the first confirmation response, observe a changed seed through original Started or Removed callbacks, restore it, and require the original pending owner to remain unknown. Normal lost confirmation still recovers without another command, and subsequent actual requests keep one listener per event.
- Condemn entry now resolves/rechecks the actor after observer installation, then performs the final full scope check before setting entered and submitting the already-resolved native actor. No native getters run between that scope check and RequestCommand. Changed/unknown scope during eligibility, observer installation or the final actor getter refuses with zero commands and cleans the unused listeners. Both reported unsafe sequences were reproduced red before the fix.
- `uv run --locked pytest -q tests/test_condemn_heretic.py tests/test_unit_effect_confirmation.py` — **184 passed**. Compileall for `src` and this test file, plus `git diff --check`, passed. Independent re-review and parent live verification remain pending.
