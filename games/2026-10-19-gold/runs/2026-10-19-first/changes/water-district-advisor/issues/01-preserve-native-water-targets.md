# B14-01 Preserve native water district targets

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Repair native PLOTS water filtering at existing query boundary, maintain native legality and read-only behavior. Worker owns focused code/tests/ticket, no live game. Record red→green and affected checks. Resolve after independent two-axis reviews and parent T68 harbor placement→T69 same-run validation.

## Offline implementation

- Removed only the blanket `IsWater` exclusion from native `PLOTS` candidates. City identity, district/build eligibility, target-array selection, impassable/mountain guards and existing adjacency estimates remain in place.
- Red→green at public `GameState.get_district_advisor` with Lua 5.1: the native harbor coordinate `(36,29)` was omitted before the fix and is returned afterward with the existing gold estimate. Mutation entry points trap writes in the native fixture.
- Native query regressions cover blocked/unrelated result arrays, land candidates, missing/empty/invalid target lists and city/build guards. `tests/test_district_advisor_native.py` plus `tests/test_district_scheduling.py`: 22 passed. Existing GameState/MCP district cases: 6 passed. `compileall` and `git diff --check` passed.
- No game connection or placement was performed by the implementer. Independent reviews and parent same-run T68 harbor placement→T69 validation remain pending.
