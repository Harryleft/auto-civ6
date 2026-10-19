# B26：城市攻击无视线拒绝发生模板类型错误

Status: ready-for-agent

## Problem Statement

T138埃利都131073攻击可见蛮族射石炮6422531/34,20，原生目标列表不包含该格，CanStartCommand=false。拒绝分支的Python模板留下Lua `{target_x}` / `{target_y}` 表字面量，拼接错误导致Runtime Error而非NO_LOS；公共owner保持unknown，后续正常游戏被阻塞。#2623首次错误，#2626同请求仅确认仍未知。#2628纯诊断原回执仍旧T137/33,21且closed，不存在本次新见证；不得据此追溯释放旧Python未决。

## Solution

原生不可攻击的目标正常返回明确的提交前拒绝，包含实际坐标；不提交攻击、不安装本次见证、不让错误文案制造未知动作。真实未知仍保留门禁，已有城市攻击确认继续严格。

## User Stories

1. As a player, I want a native blocked line of sight to return a clear refusal, so that I can choose another legal action.
2. As a player, I want the refusal to show actual target and city coordinates, so that I can understand the native limitation.
3. As a maintainer, I want generated Lua executed in tests, so that interpolation errors cannot pass through string-only assertions.
4. As a player, I want no native command on refusal, so that my city keeps its shot and the world is unchanged.
5. As a maintainer, I want no new city-command witness or listener installed before refusal, so that a rejected intent cannot masquerade as a pending command.
6. As a player, I want public MCP/GameState pending gates released only for a received definite pre-entry refusal, so that normal gameplay continues safely.
7. As a maintainer, I want a lost response or genuine post-entry uncertainty to stay unknown, so that a current NO_LOS observation never substitutes for an original command result.
8. As a player, I want existing legal city shots, outside-range/no-walls/already-fired refusals and repeated-intent confirmation behavior preserved.
9. As a player, I want the same original T138 city/target refusal verified, followed by a separate normal action/next turn/save, so that the original run continues.

## Implementation Decisions

- Fix the existing generated city-attack refusal expression at its current Lua generation seam; no new generic action framework, native setter, fallback attack or direct queue reset.
- Existing public MCP/GameState/FakeConn/native Lua tests are the approved seams. A small affected generator fix should remain small. Preserve native target/eligibility checks and command/witness confirmation behavior.
- A received explicit refusal before RequestCommand permits the existing lifecycle to release that attempted intent. Missing acknowledgement, malformed facts or later current-world queries do not prove an earlier refusal.
- Parent technical recovery explicitly restores the same confirmed T137 checkpoint and replays logged T138 choices; no favorable-result reload. Hold a fresh confirmed T138 checkpoint before final code validation. Worker owns only code/tests/ticket; parent owns live/progress.

## Testing Decisions

Execute generated Lua with T138 city131073/35,22, target34,20/unit6422531 and native target plot1234 excluded from the otherwise nonempty target list. RED must reproduce table-concat failure; GREEN must produce ERR:NO_LOS with actual coordinates, zero command, zero new witness/listener. Public tool result is refusal rather than OUTCOME_UNKNOWN, with no pending owner and independent subsequent action/save/end-turn available. Retain unknown/lost-response gates and representative existing city combat/refusal checks. Parent validates original native scenario, legal continuation and checkpoint after recovery.

## Out of Scope

Combat balance, native LOS rules, generic error frameworks, automatic alternate targets, new religious actions, production queue interpretation, rewriting historical unknown results, forced turn advancement.

## Further Notes

#2628 real currentT138 target plot1234 absent from native range target list, native CanStartCommand=false; prior closed token46b96c72749542ebbf1d4f40c271ce90/T137/33,21, unrelated to failed tokena8726ae712de4a0d80837fc4140d01dd. Old unknown owner clean-closed for pure diagnosis; no further gameplay before explicit same-run checkpoint restore. #2629 restore gold_20261019_t137_spy_apostle926620bytes dispatched once. Original world seed-1896272000/run e8b4d6a62dca49bbb256e8264beb18f2.
