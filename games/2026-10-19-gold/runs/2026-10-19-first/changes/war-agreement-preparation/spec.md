# B08：战争交易目标与失败准备退出

Status: ready-for-agent

## Problem Statement

T44，向巴西预览针对瑞典的参战交易，joint_war_target没有进入条款：只创建双方JOINT_WAR，目标value=-1且WarType=nil。准备未提交，原生界面随后把无效草稿清空；退出保护只接受旧非空指纹，导致PREPARE_EXIT_READY永久未知，其他防御操作被阻塞。

## Solution

正确绑定明确选择的第三方战争目标，依据原生可用条款处理共同宣战或加入已有战争；不从名字或固定规则猜测合法性、战争类型。不合法或含糊的条款明确拒绝。对确知从未发送、仍属于本尝试且被原生清空的失败草稿，安全关闭原会话并确认清理，恢复正常操作。

## User Stories

1. As a player, I want the selected war target encoded exactly, so that a request cannot concern an unspecified country.
2. As a player, I want an ally invited to an ongoing war through the eligible native agreement, so that defensive diplomacy can work.
3. As a player, I want mutual new wars represented only when both sides can enter the native agreement, so that ongoing-war and new-war clauses are distinct.
4. As a player, I want native war type parameters retained in the item and fingerprint, so that another war type cannot be substituted.
5. As a player, I want ambiguous native war types returned for explicit selection, so that runtime never chooses a war policy.
6. As a maintainer, I want unsupported, missing or ineligible native options refused without proposal/equalize, so that preflight failure cannot commit diplomacy.
7. As a player, I want previews to remain non-binding, so that only an explicit send/final acceptance can conclude a deal.
8. As a player, I want unknown responses confirmed without re-opening or resending, so that war-related actions remain single-submit.
9. As a player, I want an engine-cleared failed draft closed safely, so that a zero-submission failure cannot block the game forever.
10. As a maintainer, I want exact world/session/participants/no-send ownership retained before any abort, so that unrelated drafts are not discarded.
11. As a player, I want changed nonempty or unreadable drafts left unknown, so that cleanup cannot erase another negotiation.
12. As a maintainer, I want ordinary economic trade and existing acceptance protocols preserved, so that the change stays scoped.
13. As a player, I want missing choices distinguished from unreadable choices, so that unavailable war options are not guessed.
14. As a player, I want the T44 war checkpoint restored and original decisions replayed, so that the actual surprise war and its damage remain.
15. As a player, I want subsequent trade/game actions and T44→T45 confirmed, so that repair supports defense in the original game.

## Implementation Decisions

- Preserve selected target through the MCP→GameState→Lua trade item pipeline. Mirror native OnSelectAgreementOption: subtype, duration, ForType target and Parameters.WarType, using DealManager.GetPossibleDealItems for the exact participants and working deal.
- In the measured T44 case, native eligible request is counterpart→local THIRD_PARTY_WAR against target1, WarType1941490022; there are no JOINT_WAR options. Use this observed distinction rather than silently requesting a new declaration from a side already at war.
- If a war-type selector is necessary, expose a minimal optional explicit parameter on the existing propose_trade interface; when no unique native option exists, refuse and report actual eligible options for model selection. Do not rank grievances or choose policy in runtime.
- Keep fingerprints of war target/type and normal deal validation/statement receipts. Acceptance confirms the entered native deal; live checks additionally inspect actual war relationships if a war agreement is concluded.
- Failed preparation abort may accept a verified empty current draft only with matching original receipt (failed=true, sent=false), current world, exact owned original session and participants, no queued/pending replacement. Preserve the existing exact fingerprint path for matching nonempty drafts and refuse changed/unreadable nonempty drafts. No generic close, clear-working-deal or owner reset.
- Parent owns live world/recovery. Original MCP was closed only for read-only diagnostics, so explicit same-run recovery from confirmedT44 is required before continuation.

## Testing Decisions

Same already-approved public MCP/GameState/native-Lua seams. Red→green server target propagation, actual native war option binding for ongoing and new wars, missing/ambiguous/changed options, target/type fingerprint, failed-preparation native-empty cleanup, unrelated/unknown draft retention, and lost-response/no replay. Ordinary economic trade regression retained. Parent verifies eligible T44 preview, safe cleanup and next defense actions/turn, without creating unrelated test wars. No new seam.

## Out of Scope

A diplomacy planner, automatic bribes or war decisions, accepting AI counters automatically, changing resources/armies, forced war or relaxed native completion proof.

## Further Notes

#664 initial preview, #666 confirmation and #668 explicit popup cleanup stayedPREPARE_EXIT_READY. b08-native-trade.txt: observer original session2 from0 to2; receiptfailedtrue/sentfalse; original has two JOINT_WAR items withtarget-1/WarType=nil while current working deal items0. Native current scope and sid match, pending/queuedfalse. Read-only GetPossibleDealItems on the same session has only requestfrom2 THIRD_PARTY_WAR target1 WarType1941490022. Shipped DiplomacyDealView.lua OnSelectAgreementOption lines1394–1419 sets target/type; ShowAgreementOptionPopup1455–1505 reads native options. Server currently ignores numeric joint_war_target and agreement builder only sets subtype. Latest confirmed checkpoint gold_20261019_t044_war_start (#656) contains actual Sweden/Nazca war, cart85HP, allunitsalive and Canada horse trade. Replay#657–663 government/policy/research only after recovery; no world advancement since checkpoint.
