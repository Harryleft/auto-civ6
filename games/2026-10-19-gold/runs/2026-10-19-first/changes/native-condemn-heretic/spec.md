# B22：缺少原生异教徒审判动作

Status: ready-for-agent

## Problem Statement

T127瑞典向巴西宣战，研究同盟触发本方共同防御。瑞典使徒正在埃利都周围传教，刚创立的拜火教已在当地失去多数。火枪手2424845移至34,23后，与瑞典使徒5767168/owner1/religion8同格，MP1、原生CanStartCommand(CONDEMN_HERETIC,false,true)=true；unit_action condemn_heretic却返回Unknown action，零提交。玩家无法用原生允许的动作保卫宗教及军队驻地。

## Solution

通过现有单位动作工具提供原生异教徒审判，原生引擎决定资格和效果。绑定原始执行单位、同格唯一外国宗教单位以及当前世界/回合；一次提交后只确认原指令与原目标，不猜测信仰/外交支持/压力等奖励。

## User Stories

1. As a player, I want condemn_heretic in the ordinary unit action interface, so that I can perform a legal native religious defense.
2. As a player, I want the exact native command eligibility required, so that the engine owns war, congress, religion and movement rules.
3. As a maintainer, I want the original owned actor ID/type/location and current scope bound, so that a different unit cannot submit or confirm.
4. As a player, I want the actual unique same-plot foreign religious target bound, so that the command cannot silently retarget.
5. As a maintainer, I want absent, ambiguous, same-owner, nonreligious or unreadable targets refused before mutation, so that uncertainty cannot act.
6. As a maintainer, I want native false/unknown/error or missing command metadata refused without submission, so that no rule is bypassed.
7. As a player, I want exactly one RequestCommand, so that a repeated call only confirms the original action.
8. As a maintainer, I want the original native command-start and exact target-removal/effect facts observed in the same world/turn, so that unrelated removal cannot confirm.
9. As a maintainer, I want changed owner/ID/type/religion/location/turn/world and incomplete evidence to remain unknown, so that stale state cannot clear pending.
10. As a player, I want clear observed actor/target results distinct from inferred rewards, so that success does not invent favor or conversion.
11. As a maintainer, I want pending mutation/save/end-turn gates preserved, so that retries and lost responses remain safe.
12. As a player, I want T127 original legal condemnation, subsequent turn and checkpoint verified, so that the specific blocker is fixed and the same game continues.

## Implementation Decisions

- Extend the existing unit_action interface with condemn_heretic. Use existing GameState/ActionLifecycle/native Lua unit-effect seams, already approved for this run; no new public action owner or generic lifecycle framework.
- Resolve the current owned actor, exact native UNITCOMMAND_CONDEMN_HERETIC row/Hash and the unique same-plot foreign religious target. Keep the actor alive and type/location bound; target identity includes owner/fullID/unit type/religion/location. Do not expose or target spies or nonreligious units from broad map data.
- Require exact native CanStartCommand true and necessary current facts before one RequestCommand. Do not add a hardcoded war-only rule that would prohibit congress-authorized peace-time cases; do not infer congress authorization from text. Native eligibility remains mandatory in every case.
- Native UnitPanel dispatches ordinary commands through RequestCommand(selectedUnit,actionHash), and its OnUnitCommandStarted(player,unitId,hCommand,iData1) recognizes CONDEMN_HERETIC. Preserve exact started/returned evidence and original target removal/effect identity; fail closed if multiple candidates or effect evidence are unclear. Observe real native evidence before relaxing any event contract.
- A repeated same actor/action confirms only the frozen request, even if a different religious unit later occupies that tile. Read errors/cancellation/lost response do not authorize another command. Keep existing global gates.
- Update supported-action tool wording. Do not directly kill, delete, convert or change pressure/favor/religion, and do not synthesize native events. Do not claim rewards or pressure changes unless separately measured.

## Testing Decisions

Use the agreed public MCP/GameState/FakeConn/Lua5.1 seams with vertical red→green slices. Reproduce original T127 owned actor/unique native target/MP1/native eligibility true through unit_action. Cover native false/unknown/error and bad metadata, target absent/ambiguous/wrong identities/type/religion/ownership/location, no event or unrelated event/removal, actor/target changes, scope mismatch, cancellation/lost reply and same-call confirm-only/no resend or retarget. Preserve adjacent unit effects/GP/spread and other mutation gates. No source-text-only critical assertion. Parent holds original T127 actor and target, saves ready point, refreshes code without world reload, executes once and independently checks target/effects, then next turn/save. Native refusal remains factual, not an instruction to force success.

## Out of Scope

Generic religious combat, theological attacks, inquisitions, all religious-unit missions, war/deal redesign, map visibility overhaul, direct reward manipulation, fixed gameplay drivers or unrelated query cleanup.

## Further Notes

#2316 confirms bilateral Sweden war and Brazil research alliance after native defensive-war trigger. #2317 actor2424845 at35,23 fullHP. #2318 visible religious units includes apostle5767168/1/34,23/religion8. #2320 actor moved34,23. #2321 actor2424845/34,23/MP1, exact CanStartCommand=true, exact target5767168/34,23/religion8, war=true. #2322 Unknown action, no mutation. Native commandHash-597630620/Index15 (#2309). Last confirmed pre-action checkpoint T126b21verified904831bytes; parent saves current T127ready after independent orders. Same sumeria/seed-1896272000/run e8b4d6a62dca49bbb256e8264beb18f2. No pending action.
