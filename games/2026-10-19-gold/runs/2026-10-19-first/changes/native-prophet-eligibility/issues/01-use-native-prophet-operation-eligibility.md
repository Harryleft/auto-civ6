# B21-01 Use native prophet operation eligibility

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Implement through existing public unit-effect lifecycle. Worker owns necessary code/tests/ticket; parent owns live/progress. TDD native T125 false aggregate flag, zero-charge prophet, CanStartOperation true. Preserve native refusals, exact scope/identity/effect evidence and single submission. Do not infer new post-state semantics or redesign religion. Merge current integration before report; parent live verification and dual independent review precede resolution.

## Offline implementation

- Removed only the aggregate `HasReligiousFoundingUnit()==true` pre-submit veto. Original religion/belief baseline, zero-charge prophet specialization, positive movement and exact native `FOUND_RELIGION` eligibility still apply. Post-operation readiness evidence is unchanged.
- T125 public MCP/Lua fixture uses original full unit2621451/individual114,39,26,MP2.25,zero charges,false aggregate flag and native operation hash-953161477. It was red with `FOUNDING_NOT_AVAILABLE`; after the fix it submits once, remains pending until exact start/removal and increased earned beliefs, then reports readiness without claiming a founded religion.
- All prophet regressions now begin with aggregate false. Covered native false/unknown/error/unavailable eligibility with zero submission; wrong owner/full unit/individual/class/position/event; sticky changed scope; readiness without event; lost/cancelled replies and confirm-only reentry. Non-prophet activation and spread regressions remain intact.
- `uv run --locked pytest -q tests/test_unit_effect_confirmation.py` — **113 passed**. `uv run --locked python -m compileall -q src tests/test_unit_effect_confirmation.py` and `git diff --check` passed.
- Implementer did not access the live game. Independent review and parent original T125 activation/readiness/later religion-choice verification remain pending.

## Native post-state correction

- Parent #2275 receipt and religion readback, added to the spec at811888d, show exact original activation/start/removal with earned beliefs0→2, religion still uncreated and aggregate founding-readiness flag false→true. Updated only the post flag requirement to strict true; other result evidence is unchanged.
- Updated the public T125 fixture to native class4/type13 and the observed false→true transition. Before this correction it remained unknown after the exact native effect; after correction it confirms readiness with one submission. All prophet native effects now use the observed true post flag. False/nil/non-boolean/unreadable post flags remain unknown; wrong-event/identity tests also have a true post flag so that independent constraints are exercised.
- `uv run --locked pytest -q tests/test_unit_effect_confirmation.py` — **116 passed**. Compileall for `src` and this test file, plus `git diff --check`, passed. Parent restored-world live verification and independent review remain pending; no live calls by implementer.
