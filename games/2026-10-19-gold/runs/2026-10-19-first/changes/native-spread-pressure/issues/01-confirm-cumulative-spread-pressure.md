# B23-01 Confirm spread with native cumulative religion pressure

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Worker owns necessary code/tests/ticket in isolated worktree; parent owns live/progress. Read implement-spec/tdd. Public MCP/GameState/FakeConn/Lua5.1 already approved. Reproduce unchanged GetTotalPressureOnCity but exact native religion-row Pressure rises; retain exact identities, native start/charges, one submission, sticky unknown gates and lost confirmation. Fail closed malformed/ambiguous collection. No live game operations. Merge integration before report. Independent merger + Spec/Standards reviews then parent same-run T128/next-turn/save before resolve.

## Offline implementation

- Spread now reads the original religion's cumulative `GetReligionsInCity().Pressure` both before entry and during confirmation. Shipped CityBannerManager.lua:1230–1242 distinguishes this `LifetimePressure` from `GetTotalPressureOnCity`. Every returned row is validated, including the native Religion=-1 row; duplicate identities, malformed/unreadable/non-finite/negative values fail closed. A missing religion means zero only after the entire successfully returned collection passes validation.
- T128 public MCP/native-Lua fixture uses original unit2686987/religion12/city0:327684/34,26, charges3→2. Parent #2377 verified pre rows (-1/50,8/26.078125,12/17.90625); #2373 verified post12/217.90625 while the other reading remains5.96875. The old reader remained unknown; cumulative row evidence is green and reports only the original spread pressure increase.
- Spread reuses the adjacent scalar identity matcher for the original actor/religion and city, with sticky known changes and recoverable unknown reads. It checks identity/baseline/scope after arming and before one operation, then checks final identities/scope after reading pressure. Required original start, exact charge decrease or exact final-charge removal remain. The existing two bounded spread listeners survive a lost confirmation reply and are replaced on the next actual unit-effect request; other GP behavior is unchanged.
- Red→green slices cover the native pressure mismatch, lost-confirmation scope invalidation, restored actor/city identities, and unavailable actor type. Regressions cover complete missing-zero baselines, malformed/duplicate/unavailable tables before and after entry, same/lower/wrong-religion pressure despite another reading increasing, final-charge removal, retries, lost submit/confirmation, cancellation through existing unit-effect cases, pending save/turn gates, and scope changes during eligibility/final pressure read.
- `uv run --locked pytest -q tests/test_spread_pressure.py tests/test_unit_effect_confirmation.py tests/test_condemn_heretic.py` — **251 passed**. `uv run --locked python -m compileall -q src tests/test_spread_pressure.py tests/test_unit_effect_confirmation.py` and `git diff --check` passed.
- No live game operations by implementer. Independent review and parent restored same-run T128 spread/next-turn/checkpoint validation remain pending.

## Independent review and original T128

9538daf...97bdf16 final Spec/Standards: no material issues found. Parent #2391 explicitly restored same confirmed T128, original units/cities verified. #2377 real pre cumulative17.90625/native incoming5.96875; #2409 final ready checkpoint911719bytes. Clean refresh only, no further world reload; #2411 one spread confirms17.90625→217.90625/charges2, #2412 independent city拜火2/3; #2413 saves T128 confirmed911813bytes. Subsequent turn/save remains pending while independent B24 travel implementation finishes.
