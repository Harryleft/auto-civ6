# B16-01 Respect native local candidate eligibility

Status: pending-review
Dependencies: none
Spec: ../spec.md

Verify the native claimant contract and repair the narrow candidate refusal through existing public boundaries. Implementer owns necessary pool code/tests and this ticket only; not parent progress or live game. Use TDD red→green, affected checks, independent Spec/Standards review, then parent same-run recruitment/activation/later-turn checkpoint before resolving.

## Implementation

- The shipped native current-timeline UI checks each Can* independently; Claimant only filters the past timeline. Removed the extra Claimant eligibility assumption while keeping unique current individual, class/cost/balance validation, exact native Can* == true, scope/token and existing single-submit confirmation.
- TDD: the exact T73 individual 133 / class 5 / Claimant 0 / 82.296875 points fixture first failed with CANDIDATE_UNAVAILABLE. After the narrow correction, the public MCP result confirms a new individual 133 unit and exactly 80 points spent.
- Public Lua-seam regression covers local and unclaimed recruit/reject/gold/faith patronage, each operation and currency's independent permission, native false/unknown/malformed refusal, foreign native ineligibility, missing/duplicate candidate, and existing resource/identity/scope/lost-response confirmation.
- Offline validation: `uv run --locked pytest -q tests/test_great_person_pool_confirmation.py tests/test_game_state.py tests/test_server.py` — 398 passed. `uv run --locked python -m compileall -q src tests` and `git diff --check` passed.
- Independent Spec/Standards review and parent same-run live validation remain pending.
