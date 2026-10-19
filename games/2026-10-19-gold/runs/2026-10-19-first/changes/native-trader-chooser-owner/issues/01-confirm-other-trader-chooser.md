# B25-01 Confirm completed route with another trader's chooser

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Worker owns necessary trader code/tests/ticket in isolated worktree. Parent owns live/progress. Read implement-spec/tdd; public MCP/GameState/FakeConn/nativeLua approved. Strict original route/scope and verified current other trader UI ownership; no automatic economic action or confirm-time UI mutation. Preserve same original chooser and unknown guards, lost reply/cancel/pending gates. Parent holds real T131 receipt/other idletrader for pure native proof after explicit UI workaround confirmed the original pending owner. Final native proof + public new trader route + nextturn/save required. Merge integration before report, no live game by worker.

## Offline implementation

- Added a current chooser-owner proof using native `UI.GetHeadSelectedUnit`, matching shipped TradeRouteChooser.lua:1030/1061/1136. The other selected unit must be owned, a different full ID, native trade-capable, awake with finite positive movement, consistent through UI/UnitManager/player-unit lookups, on a current owned city, and absent from the complete outgoing route records. This context is read afresh on each confirmation and is not stored as another economic intent.
- Original route facts remain first. A verified other trader's MAKE_TRADE_ROUTE chooser is preserved during entry cleanup and accepted by pure confirmation without UI writes or another route operation. Same original chooser and incomplete/foreign/nontrader/active/no-movement/inconsistent selection do not receive that exemption. Teleport and legacy B05 cleanup retain their existing behavior.
- Original actor/city identities are observed independently, including mixed unavailable readers and valid mismatches; known changes latch invalid across restoration. Final scope is checked after every query outcome, including failed getters, so an unknown UI read cannot hide a known scope change. Current UI changes may be reevaluated without invalidating the frozen original economic intent.
- T131 public MCP/native-Lua red→green fixtures cover both the entry cleanup incorrectly closing the other chooser and delayed original-route confirmation incorrectly remaining unknown. The fixture binds original1966090/type2/0:196610→0:65536,active2→3/cap4 and other2883596/40,25/MP2/nativeAWAKE1797587246. Further regressions cover original economic failures, original/current identity distinctions, mixed unknown/mismatch reads, final getter scope changes, lost/cancelled replies, and save/turn/mutation gates.
- `uv run --locked pytest -q tests/test_trader_chooser_owner.py tests/test_trader_confirmation.py` — **115 passed**. `uv run --locked python -m compileall -q src tests/test_trader_chooser_owner.py` and `git diff --check` passed.
- No live game operations by implementer. Parent #2511 establishes native RED against the retained original receipt; final pure native GREEN, preserved chooser/selection, public new-trader route and next-turn/checkpoint evidence remain pending, along with independent review. No pending owner recreation or original route replay is required.
