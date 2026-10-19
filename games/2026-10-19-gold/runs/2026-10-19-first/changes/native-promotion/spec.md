# B10：通过原生命令执行单位晋升

Status: ready-for-agent

## Problem Statement

T52，65HP弓箭手655363获得晋升，模型选择齐射增强反击并获得游戏规定的恢复。当前promote_unit虽然受动作所有者管理，但在GameCore直接SetPromotion、ChangeStoredPromotions和SetDamage(0)，不是普通玩家晋升，不能用于本局。只读原生CanStartCommand已确认该弓箭手可PROMOTE、原生选项为VOLLEY Index14与GARRISON Index15；因此旧兼容写法阻塞了正常晋升。

## Solution

让promote_unit镜像已安装游戏UnitPromotionPopup/UnitPanel的原生PROMOTE命令，原生引擎决定晋升、经验、伤害恢复和行动力消耗。读取并绑定当前可用的原生晋升选项；只提交一次并在原世界/回合/单位上确认请求结果，不回退直接修改状态。

## User Stories

1. As a player, I want promotions executed through the ordinary native command, so that normal rules govern the game.
2. As a player, I want my chosen promotion bound to the exact current native option, so that another promotion cannot be substituted.
3. As a player, I want the native engine to apply HP recovery and movement cost, so that the tool never invents benefits.
4. As a maintainer, I want direct SetPromotion/ChangeStoredPromotions/SetDamage removed from this gameplay path, so that no compatibility fallback cheats.
5. As a player, I want unavailable or unreadable native choices refused before entry, so that the tool never guesses eligibility.
6. As a maintainer, I want world, turn, owner, composite unit identity and request token retained, so that stale or reused-index units cannot confirm.
7. As a player, I want unknown or lost responses observed without resubmission, so that promotion is single-submit.
8. As a player, I want partial results retained as unknown, so that later confirmation cannot reapply healing or XP changes.
9. As a maintainer, I want exact promotion result and native command ownership confirmed, so that unrelated existing promotions do not establish success.
10. As a player, I want other transformations retained, so that upgrade/delete behavior stays stable.
11. As a player, I want accurate completion wording and relevant native results, so that task entry is not confused with applied promotion.
12. As a player, I want the original T52 archer promoted and subsequent game actions/T53 verified, so that this repair unblocks the same war.

## Implementation Decisions

- Replace the existing direct GameCore compatibility writes for promote_unit with the shipped UI native UnitManager.RequestCommand(unit, UnitCommandTypes.PROMOTE, parameters), using PARAM_PROMOTION_TYPE and the exact eligible option value from CanStartCommand results. The observed values are indices14/15, not promotion hashes664752614/-1280436412.
- Revalidate the full current native option and unit identity immediately before entry. Missing/false command eligibility or unreadable option results authorize no submission. Runtime chooses no promotion policy.
- Keep one ActionLifecycle owner and explicit submit/confirm contexts. Bind intent and receipts to original unit/world/turn/token; no command fallback, synthetic event, damage/XP mutation or second dispatch on retry.
- Confirmation establishes the exact chosen promotion on the bound unit after entered native dispatch, with whatever additional native event/receipt proof is required by existing domain standards. Do not assume full healing, zero stored promotions or fixed movement cost from the old compatibility code; read actual values and report/verify them as native facts.
- Update obsolete architecture guidance that endorses the direct GameCore path. Preserve unrelated transformations and public interface unless a minimal correction is necessary.
- Parent owns live game. T52 checkpointgold_20261019_t052_before_promotion is confirmed, no pending action or old promotion submitted. Fresh MCP after merge suffices; no strategic rollback is needed.

## Testing Decisions

Use the previously approved public GameState/MCP/native-Lua fixtures. Red→green eligible native option dispatch, exact index parameter, direct mutator absence, eligibility/option failures and altered choices, owner/fullID/world/turn/token refusal, response loss/cancellation/no-replay and partial-result retention. Keep ordinary upgrade/delete and relevant action lifecycle regressions. Parent chooses VOLLEY on archer655363 and observes native promotion, HP/movement and T52→T53 in the original game. Existing candidate listing may remain read-only; mutation must use native options, not the old broad eligibility list.

## Out of Scope

Promotion planner, automatically selecting promotions, direct damage/XP/stored-promotion manipulation, compatibility fallback, unrelated combat redesign or forced XP for tests.

## Further Notes

Lastnative save#878 T52, same run e8b4d6a62dca49bbb256e8264beb18f2. #879 read-only CanStartCommand(unit,PROMOTE,true,true) for655363=true; UnitCommandResults.PROMOTIONS {14,15}, mappingVOLLEY/GARRISON; command=-1259482049, param=-927495623. Same read forcart262146=false while on HEAL (do not infer why or auto-wake). Existing get_unit_promotions lists candidate types; no old mutation invoked. Shipped UnitPromotionPopup OnPromoteUnit lines58–73 and UnitPanel OnPromoteUnit2706–2715 submit onlynativecommand. Prior comment claiming native returned without applying is historical, not current proof of unavailability.
