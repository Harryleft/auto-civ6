# B20-01 Use current native alliance action eligibility

Status: resolved
Dependencies: none
Spec: ../spec.md

Implement spec through existing public relationship lifecycle; own necessary code/tests/ticket, parent owns live/progress. TDD exact T108 red→green and relevant fail-closed/type/query/lost-response cases, merge current integration before reporting commit. Independent merger and two-axis review; resolve only after parent same-run proposal/later-turn checkpoint. No unrelated deal construction redesign.

## Offline implementation

- Query and execution share native typed eligibility: verify the current `Alliances`, `DiplomaticActions`, and `DiplomaticActions_XP1` binding, then require `IsDiplomaticActionValid(actionType, target, true)` to return a boolean true. This follows shipped `DiplomacyStatementSupport.lua:167` and the XP1 alliance/action binding used by `DiplomacyActionView_Expansion1.lua:82–86`; parent #2004 confirmed the research binding in this world.
- Removed the alliance-only Diplomatic Service gate. The delegation obsolescence diagnostic remains unchanged. Generic query eligibility means at least one supported native alliance type is eligible; execution checks the exact selected type before any deal/session mutation. Native false, unknown, malformed metadata, and unavailable validators refuse without submission.
- T108 public MCP reproduction was red with `NO_CIVIC` and is green with an exact research proposal/confirmation. Public trade-options and rich diplomacy eligibility were independently red, then green. Tests also cover another-type-only eligibility, zero-write refusals, relationship/pending-deal boundaries, exact-alliance no-op, bilateral confirmation, and lost replies with a single proposal sequence.
- Validation: `uv run --locked pytest -q tests/test_alliance_eligibility.py tests/test_alliance_confirmation_lua.py tests/test_relationship_lifecycle.py tests/test_civilization_identity.py` — **129 passed**. `uv run --locked python -m compileall -q src tests/test_alliance_eligibility.py tests/test_alliance_confirmation_lua.py` and `git diff --check` passed.
- No live game operations by the implementer. Independent review and parent same-run T108 proposal/later-turn checkpoint remain pending; native eligibility does not guarantee AI acceptance.

## Review and live validation

Implementation f06742d, fast-forward integration. Independent Spec and Standards reviews of e5c846b...f06742d both found no material issues. 129 related tests and compile/diff checks passed. Parent clean-refreshed MCP without reloading the world: #2005 original T108; #2006 query eligible; #2007 CivilService=true/DiplomaticService=false/nativeResearchValid=true; #2008 one proposal ACCEPTED; #2009 independent bilateral RESEARCH Lv1 confirmation. #2011–2016 handled ordinary completion popup and Swedish dialogue, then confirmed T108→109. #2017 saved gold_20261019_t109_b20_verified,868073bytes. #2018 DiplomaticService56% boosted,4turns remaining. Original run unchanged; no civic/relationship setters.
