# B15-01 Confirm native partial move witness

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Research native partial movement event contract and implement minimal request-bound confirmation at existing move boundary. Worker owns necessary code/tests/ticket only; parent captures live evidence and performs same-run recovery. Red→green, affected checks, independent two-axis review, then parent T70 checkpoint before resolving.

## Implementation and offline validation

- Native sources: `UnitPanel.lua:2424` and `TutorialUIRoot.lua:2469` name the `UnitMoveComplete(player, fullID, x, y)` position fields. `ActionPanel.lua:819` handles `SegmentComplete` separately. The alternative confirmation uses the exact chain captured by parent in `evidence/b15-native-move-events.txt`, not the callback name or position change alone.
- Optional bounded observer: after native submission entry, match `Added(slot=0, MOVE_TO.Index)` → `MoveComplete(fullID, x, y)` → `Deactivated(MOVE_TO.Hash, 1)` → `Cleared(0, 0)`. MOVE_TO metadata comes from native GameInfo. Old ALERT cleanup before Added is ignored. Missing observers retain the existing SegmentComplete contract without contributing alternative evidence.
- Confirmation requires native return, original token/world/turn/player/full ID/origin/intent, observed coordinates equal to current coordinates, actual displacement, a successful nil queued-destination read and idle/non-processing UI. Known scope changes remain invalid; unknown event scope remains uncertain. The result reports `PATH_INTERRUPTED (native move cleared)` and the actual coordinate/remaining moves.
- Confirmation keeps the four alternative listeners until the next actual move request; a lost confirmation reply cannot detach later invalidation. Repeated unknown requests, native exceptions and cancellation retain the original ActionLifecycle owner and do not resubmit.
- Red→green: the exact T69 chain initially remained OUTCOME_UNKNOWN; after the fix it reports `(42,27)`, MP1 and an interrupted path. Focused native chain, existing movement/confirmation/scope and cancel regressions: 335 passed. `compileall` and `git diff --check` passed.
- No live calls or parent progress edits by the implementer. Independent review and parent same-run follow-up decision/T69→T70 checkpoint remain pending.

## Review correction: scope history fences all move outcomes

- Reproduced the blocking Standards finding through GameState/Lua: after the new observer recorded a changed seed, restoring the seed plus an exact queued target and SegmentComplete incorrectly confirmed the old move.
- Reserved native `invalid` for known scope mismatches; alternative-chain ordering/payload errors use `chainInvalid`. SegmentComplete now also preserves known scope mismatch history. Query guards before/after all native evidence emit a token-bound `MOVE_SCOPE_INVALID` receipt; ActionLifecycle latches it before interpreting any movement outcome.
- Red→green covers the reported bypass and all existing arrival/exhaustion/ZOC paths, an independent valid SegmentComplete after only chain mismatch, scope change before a response completes, and recovery from a query-only unknown scope. Six directly affected test modules: 344 passed; `compileall` and `git diff --check` passed.
- Parent reported same-run live verification #1293 PATH_INTERRUPTED, separate #1298 movement and #1303 T70. This review correction still requires independent recheck; ticket remains implemented-pending-review.
