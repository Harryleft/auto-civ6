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
