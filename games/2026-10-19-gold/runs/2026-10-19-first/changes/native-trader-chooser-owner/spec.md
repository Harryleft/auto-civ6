# B25：另一名商人的路线选择界面阻塞已完成商路

Status: ready-for-agent

## Problem Statement

T131尼普尔空闲商人1966090建立国内商路至乌鲁克40,25，原生商路已存在，active2→3/cap4；tool却持续ROUTE_ESTABLISHED_INTERFACE_CLEANUP_UNCONFIRMED。原商路动作后游戏自动选中了首都的另一名空闲商人2883596/40,25，并打开MAKE_TRADE_ROUTE。界面mode名称相同，实际界面归属已改变。旧确认仅要求mode!=chooser，造成原完成动作与另一单位待办互锁。原生显式Escape退出后同请求仅确认通过，未重发商路。

## Solution

以原商路事实确认经济动作，以当前原生选中单位证明同名chooser是否属于另一名商人。保留其选择界面与玩家下一意图，不把另一单位的合理界面当成旧动作未收尾；身份不清时仍未知。

## User Stories

1. As a player, I want a completed route confirmed while another ready trader owns the same chooser mode, so that multiple idle traders do not deadlock one another.
2. As a maintainer, I want original actor/fullID/type/cities/route and scope facts retained, so that a UI change cannot substitute for missing economic evidence.
3. As a player, I want the new trader chooser preserved, so that confirming an old route does not cancel a different unit's current view.
4. As a maintainer, I want a strict current native selected owned fullID/type/location/activity/movement and idle route state verified for the other trader, so that a different selected value alone cannot waive cleanup.
5. As a player, I want the original actor still owning the same chooser to remain unknown until genuine cleanup, so that the existing B05 protection remains.
6. As a maintainer, I want absent/unreadable/foreign/nontrader/inconsistent selected identity to remain unknown, so that uncertainty never implies another valid chooser.
7. As a maintainer, I want scope rechecked after native getters and known original scope/entity changes latched, so that unknown fields cannot hide invalid original evidence.
8. As a player, I want retry/lost response/cancel to confirm only, so that route operations and cleanup are never silently replayed.
9. As a maintainer, I want existing global pending save/end-turn/mutation gates preserved, so that unrelated operations cannot bypass an unknown route.
10. As a player, I want the actual T131 original route and other chooser proof validated, followed by the new trader route/next turn/save, so that this specific scenario continues in the same game.

## Implementation Decisions

- Extend the existing trader-action preparation/query seam and ActionLifecycle owner only. Existing public MCP/GameState/FakeConn/nativeLua seams approved; no new generic UI manager or background observer.
- Exact original route, actors, cities, active/capacity counts and scope must be established before UI readiness. Same chooser mode alone does not prove same UI ownership. For trade_route, a complete current native head-selected other owned awake idle trade-capable unit with fullID/type/location consistent through native lookups can prove a different current chooser; unknown facts refuse confirmation. Native shipped TradeRouteChooser Refresh/Open reads UI.GetHeadSelectedUnit; CheckNeedsToOpen automatically opens mode for an owned awake trade unit with movement, invoked by UnitSelectionChanged.
- Do not close a verified different trader's current chooser during original action cleanup, nor mutate any UI in confirm-only queries. Original same-trader chooser stays strict. Unrelated modes, teleport/TradeOriginChooser and other domains retain their existing boundaries.
- The other selected trader is current UI context, not a new economic intent or frozen original action identity. Its legitimate selection changes may be evaluated afresh; original actor/cities/scope remain frozen and sticky. Never retarget/replay either trader's economic operation from confirmation.
- Validate exact current native facts, fail closed malformed/inconsistent data and unknown scope, perform final scope verification after getters. No resource/unit/movement setters, simulated events, automatic next trader route or synthetic completion.

## Testing Decisions

Public MCP/GameState/nativeLua RED→GREEN T131 original route1966090/0:196610→0:65536 active3/cap4, UI MAKE_TRADE_ROUTE but head-selected own idle2883596 at40,25. Assert one original route operation and no UI reset of the other chooser; repeated original call confirms only. Cover original same chooser, unknown/foreign/nontrader/active/noMP/identity inconsistent selected facts, false route/city/count facts, valid UI changes, lost replies/cancel, scope after getter and sticky original changes, pending gates; preserve existing trader transfer and B05 cleanup. Parent ordinary T131 #2504 was confirmed only after explicit native UI Escape (documented workaround); retain original Lua receipt, keep T131, fresh final code without world reload. Reopen the other trader's real chooser through ordinary native UI, exercise final pure native confirmation against the frozen original receipt, prove interface/selection preserved; offline public tests cover the pending Python owner. Then establish the new trader route through public MCP and next-turn/save on final code. Report these evidence levels explicitly; do not recreate a pending owner or resend the completed original route.

## Out of Scope

Generic popup dismissal, auto cycling policy changes, universal UI ownership framework, routes/rewards automation, resource trade quantities, replacing active routes, replaying old economic actions, other modes or combat redesign.

## Further Notes

#2497 one original route submitted; #2498 route exists; #2499 modeMAKE and firstready2883596. #2500 genericpopupclose did not fix; #2501 originalconfirm-only stillunknown. Native screenshot shows chooser origin乌鲁克 and the new capital trader. Two explicit Escape actions made #2504 originalsamecall confirm; #2505 savedgold_20261019_t131_b25_ui_recovered911846bytes. #2506 original r.cleanupOK=true, token6ac484a40c714d9f84b39d4f57b12130/unit1966090/type2/origin0:19661037,28/dest0:6553640,25/worldT131seed-1896272000/enteredreturnedtrue/active2/cap4; currentselectedowner0/full2883596/40,25, currentmodeSELECTION. Originalreceipt persists in native Lua; no pending. Same original run e8b4d6a62dca49bbb256e8264beb18f2. B24 traveller dueT132 remains in transit; B23 original spread and T129/save already passed. Do not replace world unless technical recovery becomes necessary.
