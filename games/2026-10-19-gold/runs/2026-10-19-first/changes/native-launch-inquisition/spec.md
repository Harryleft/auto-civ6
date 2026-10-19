# B27：原生可启动宗教审判，但工具没有该动作

Status: ready-for-agent

## Problem Statement

原局T139瑞典新教已支配其他三个文明，本方仍由首都/埃利都/拉格什三城的拜火教保持独立。使徒3145737/owner0/type4/34,24/4MP/3charges/religion12已原生允许UNITOPERATION_LAUNCH_INQUISITION，玩家宗教HasLaunchedInquisition=false；但#2663 unit_action('launch_inquisition')被工具列为未知动作，零提交。需要启动原生宗教审判，允许后续购买审判官清除本方城市的外来宗教。

## Solution

增加一个明确的使徒原生动作。引擎决定资格、使徒消耗和审判官解锁；工具只提交一次，绑定原实体与宗教，以原生事件及false→true结果确认，不以单位消失代替完成。

## User Stories

1. As a player, I want an eligible owned apostle to launch an inquisition through the public unit action, so that I can buy native inquisitors.
2. As a player, I want native CanStartOperation to decide eligibility, so that charges/location/resources are not replaced by guessed rules.
3. As a maintainer, I want original fullID/type/owner/religion/position and own founded religion bound before entry, so that an index collision or foreign unit cannot substitute.
4. As a player, I want an already-launched or unreadable pre-state refused without submission, so that the once-per-game outcome is not invented or repeated.
5. As a maintainer, I want the exact original Started and Removed events witnessed after entry in either native order, both native unit lookups absent and the bound player's inquisition flag changed false→true, so that absence alone never implies launch.
6. As a player, I want normal engine consumption of my apostle preserved, so that the tool does not modify charges, movement or units directly.
7. As a player, I want missing/wrong events, unknown flags or partial absence to remain pending, so that the tool cannot claim success prematurely.
8. As a maintainer, I want final scope checks after the last getter and sticky known identity/scope changes, so that temporary unreadability cannot hide invalid evidence.
9. As a player, I want repeat/lost-response/cancel handling to confirm only, so that a consuming operation is never replayed.
10. As a maintainer, I want global mutation/save/turn gates kept while pending and released after confirmed launch, so that the existing lifecycle owns this action.
11. As a player, I want existing activate/spread/condemn and unit actions unchanged, so that this addition stays limited to launching the inquisition.
12. As a player, I want the same T139 apostle/flag transition, later legal continuation and next-turn/checkpoint verified, so that the original game continues.

## Implementation Decisions

- Extend the current public unit_action and GameState/ActionLifecycle with one consuming native religious action, using the existing operation witness pattern. Prefer a small cohesive implementation; no generic observer framework, separate lifecycle or background polling.
- Use the actual GameInfo.UnitOperations row hash. #2659 rowHash=-1128148313, UnitOperationTypes.LAUNCH_INQUISITION is nil, while CanStartOperation(unit,row.Hash,nil,{}) is true. No guessed enum fallback, setter or simulated event.
- HasLaunchedInquisition is a callable native player-religion getter; #2661 givesfalse, own GetReligionTypeCreated=12 and exact actor religion12/row.LaunchInquisition=true/row.SpreadCharges3. Bind original native identity and scope before final eligibility recheck/entry.
- Primary shipped Units.xml and Units_Text.xml specify that launching consumes the apostle. Native Started plus original removal, absence through both lookups and own flagfalse→true collectively prove the requested result. Do not hard-code a presumed charge decrement or keep the apostle alive.
- All current and final getters fail closed; collect readable original identity/scope changes even alongside unknown fields and latch invalidation. Confirmation is read-only and never resubmits or mutates UI. Preserve observers/facts across lost acknowledgements and responses.
- Existing approved public MCP/GameState/FakeConn/nativeLua seams suffice; no further permission needed. Worker owns code/tests/ticket; parent holds original T139 world and handles native validation.

## Testing Decisions

TDD through public MCP/GameState and executed native Lua: T1393145737/type4/owner0/religion12/34,24/MP4/charges3/preflagfalse, hash-1128148313, native eligibletrue. Exactly one RequestOperation, actual Started/Removed both orders plus dual absence and flagtrue confirm; negative native eligibility/preflag/owner/type/religion/identity/scope, malformed getters/events and partial absence remain refused or pending as appropriate. Verify mixed unknown/valid changes, final getter scope, change-and-restore invalidation, lost/cancelled replies, repeated original confirmation and mutation/save/turn gates. Preserve existing unit actions. Parent#2664 saved original gold_20261019_t139_b27_ready928587bytes; final clean code refresh without world reload, actual one consuming operation/flag/new inquisitor eligibility, next-turn/save before resolve.

## Out of Scope

Purchasing or automatically commanding inquisitors, removing heresy, evangelizing beliefs, automatic belief or promotion choice, spreading religion redesign, religious combat, native rule changes or resource/unit setters.

## Further Notes

#2659 real actor eligibilitytrue/4MP/3charges/full3145737/type4/34,24. #2661 flagfalse, own founder12 and actor12. #2663 public unsupported actionzero submission. #2664 T139 ready checkpoint928587bytes; same seed-1896272000/run e8b4d6a62dca49bbb256e8264beb18f2. No current pending mutation. T139 other independent orders already complete: firstZiggurat36,24, EriWorkshoprepair2t, city+Field destroy Bombard6422531 (dualabsence #2659), newBuilder3211267skip; oldBuilder consumed by finalcharge. Hold this T139/apostle for final native proof.
