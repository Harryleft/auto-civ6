# B19-01 Bind explicit governor destination owner

Status: resolved
Dependencies: none
Spec: ../spec.md

Implement spec through existing governor MCP/GameState/Lua boundaries, TDD red→green. Own necessary code/tests/ticket; parent owns live/progress. Confirm base integration, preserve others. Finish relevant checks, merge integration tip, report commit for merger and independent two-axis review. Resolve after parent same-run assignment/turn/checkpoint evidence.

## Implementation and offline validation

- Added optional `target_owner` to MCP/GameState assignment, resolved to the native local owner when unspecified. Explicit owner/city IDs require strict integer input; malformed values never choose a default. Destination owner is distinct from the initiating local player and is included in the intent, receipt, no-op comparison, native city lookup, `PARAM_PLAYER_ONE` and exact assigned-city readback.
- Native `Expansion2/UI/Additions/GovernorAssignmentChooser.lua:403` binds destination owner to `PARAM_PLAYER_ONE`; native CanAssignGovernor remains the eligibility authority. Known assignment scope changes persist across readbacks, including establishment-report getters; unknown query scope can recover. Assignment is confirmed independently from later establishment.
- Target discovery uses existing tools: get_city_states identifies the city-state player. For a city-state capital, a pure run_lua read can inspect `Players[5]:GetCities():GetCapitalCity()` and print `GetOwner()`, `GetID()`, `GetX()` and `GetY()` before passing that exact tuple. No new city query or governor policy was added.
- Red→green at public MCP/GameState/Lua 5.1: owner5/city65536 reaches the foreign city at40,6 despite the own owner0/city65536 at40,25. Native eligibility and submission receive owner5 while actor remains0. Default own calls and explicit0 stay compatible; a changed pending owner is refused until the original request is confirmed, with no automatic reassignment.
- Governor target-owner and existing governor tests: 80 passed, including an in-memory MCP wire session with schema/strict-argument validation, native args, no-ops, stale receipt/owner/fullID/coordinates, refusal, delayed/lost/cancelled responses, scope history and appoint/promote compatibility. `compileall` and `git diff --check` passed.
- No implementer live calls or parent progress edits. Ticket remains pending independent review and parent T91 foreign assignment/turn/checkpoint evidence.

## Independent review and live validation

880f1f9; Spec and Standards reviews of a18a1dd...880f1f9 found no material issues. Parent clean-refreshed MCP with no world reload or pending request. #1735 originalT91 confirmed, #1737 assignedAmani targetowner5/city65536 (establishment5turns), #1738 independentread assignedowner5/fullID65536/40,6. #1739 T92 and #1741 checkpointgold_20261019_t092_b19_verified835258bytes confirmed. Establishment and suzerain effects require later turns; no artificial envoy or immediate establishment claimed.
