# B13：推进请求触发本地单位待办后的闭环

Status: resolved

## Problem Statement

T57警戒已确认后，#1046 end_turn的请求使原生探索战车262146在35,18变为ready，阻塞类型ENDTURN_BLOCKING_UNITS。当前仍T57、local active/complete=true、processing=false、HasSentTurnComplete=false；工具保留pending。但#1049明确移动被服务层pending门禁拒绝，#1051仅确认仍保留请求，玩家不能处理原生要求的单位待办。

## Solution

以本次原生请求的可核实拒绝/本地阻塞见证，区分“原生推进已发出，结果仍未知”和“本次尝试因本地待办结束，未推进”。只有后一种能确认并退休本次尝试，返回明确待办，随后模型处理单位再发起新的推进。无充分见证仍保持未知并走显式恢复，禁止把普通查询或瞬时未发送状态当作取消。

## User Stories

1. As a player, I want native local blockers surfaced after an end-turn attempt, so that I can respond to the engine.
2. As a player, I want a conclusively locally rejected attempt retired, so that its unit blocker can be handled.
3. As a maintainer, I want request-specific native evidence, so that an unrelated ready unit cannot cancel a pending turn.
4. As a maintainer, I want world/player/turn and attempt identity bound, so that stale evidence cannot retire another attempt.
5. As a maintainer, I want missing or ambiguous evidence kept pending, so that lost responses never cause a replay.
6. As a player, I want repeat end_turn to confirm the original attempt only, so that an old request is never resent.
7. As a player, I want subsequent end_turn after an explicitly resolved local rejection to be a new attempt, so that native units and game rules remain authoritative.
8. As a player, I want read-only status queries to remain observational, so that they cannot erase unresolved state.
9. As a maintainer, I want already-sent, unready, busy, changed-world and unknown readiness guarded, so that live turn processing stays protected.
10. As a player, I want ordinary model-selected unit actions used to resolve the blocker, so that no forced end-turn, automatic skip or strategy policy is introduced.
11. As a player, I want explicit same-run recovery before reproducing this new receipt, so that the old request without new evidence is not guessed away.
12. As a player, I want T57→T58 and a confirmed checkpoint verified, so that both B12 and B13 demonstrably unblock the run.

## Implementation Decisions

- Inspect the native ActionPanel/end-turn and notification lifecycle to find an authoritative request-bound local rejection witness. Prefer existing TurnProgress, native Lua and GameState/MCP seams. A final fresh no-sent/blocker observation alone is insufficient to release the owner.
- Bind a receipt/token and exact original identity, arm only required native witnesses before entering this request, track entry/return and relevant native state/events; retain unknown if proof cannot distinguish completion in flight. No generic reset, no transparent replay, no synthetic events or direct game-state writes.
- An explicit end_turn confirmation may acknowledge the original attempt as conclusively locally blocked only when proven by the native contract. Return that no turn advanced and name the blocker; this must not submit another action in the same call. get_turn_status never retires requests. If native evidence cannot prove rejection, preserve pending and document it; do not weaken the guards to make a test pass.
- Once a rejected attempt is explicitly resolved, normal unit/production choices and a separately requested fresh end_turn can proceed. Preserve diplomacy/congress/information and existing game-over/advance confirmation behavior.
- Parent owns live game. Before fresh receipt testing explicitly restore same-run gold_20261019_t057_before_b12, reconfirm alert with B12, then reproduce exploration wake-up. Worker owns code and offline tests only.

## Testing Decisions

Use already approved public MCP/GameState/native Lua fixture seams with red→green. Reproduce request→native local block→explicit confirmation→normal unit choice→new request→advance, asserting one submit per attempt. Cover delayed reply, absent/mismatched/late event, wrong token/unit/world/turn, read errors, true/unknown sent/unready/busy, vanished blocker and external turn change; all ambiguous cases retain pending and never resend. Pure status reads cannot release. Preserve ordinary turn, trade/congress/popup and lifecycle gates. Parent verifies T57→T58 and same-run save.

## Out of Scope

Automatic unit moves/skip/force-end, changing user options, generic cancellation of unknown requests, background drivers, diplomacy redesign or unrelated fixes.

## Further Notes

Evidence tool-calls.jsonl #1044–1051. Native shipped Base ActionPanel DoEndTurn handles local blocking units by selecting a ready unit; RequestAction is used only without such a blocker. Native engine can reveal a newly awake unit after RequestAction. Existing SUBMITTED currently only means the Lua RequestAction call returned; it is not proof HasSentTurnComplete became true. Original T57 identity CIVILIZATION_SUMERIA/-1896272000/player0, same run e8b4d6a62dca49bbb256e8264beb18f2. Latest pre-pending confirmed save #1042 gold_20261019_t057_before_b12.

## Native evidence refinement

诊断提交8eb37f1／集成0be1adf；#1057单次T57提交，token01bdbe67f1514c5ba9e87caba668c287。entered/returned=true，RequestAction返回nil，不能据返回值认定拒绝。真实顺序：LocalPlayerTurnEnd → UnitOperationDeactivated(0,262146,-1676983098,1) → UnitOperationsCleared → LocalPlayerTurnBegin，全部同scope CIVILIZATION_SUMERIA|-1896272000|0|57；随后BlockingChanged(0,23669119)、PlaybackComplete，cart ready=true。原生是同回合重新开启，不是“仍等其他玩家”。以精确本次End→Begin同回合见证及最终原生待办/未送完成/非忙/不可unready等完整事实确认本次attempt为REOPENED_LOCAL；普通blocker或Publish单独仍不足。只在显式end_turn确认退休本次attempt，返回未推进及新待办，不自动重发。最小化最终观察器，移除无用泛诊断。

Final validation: d2a5c1d／a0e3e5c原生重开闭环；审查补修4c4bf42／0603f17、d71b4dd／f135b81，最终Spec/Standards均无实质问题。446相关五模块通过；最终末尾guard变更后68项turn_reopened重跑通过，compileall/diff-check通过。实机#1066保留pending、#1067纯查询不释放、#1068REOPENED_LOCAL、#1070正常单位移动、#1073新请求T58、#1074同轮保存；最终代码刷新后#1136继续T62→T63，未回档，未制造丢回复。
