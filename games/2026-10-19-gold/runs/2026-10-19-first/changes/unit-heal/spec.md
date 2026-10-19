# B09：休整任务原生开始确认

Status: ready-for-agent

## Problem Statement

T48战车85HP，在学院工地(34,22)选择heal，原生entered/returned/started均为true，ready=false，但activity仍为OPERATION而非HEAL。现有确认只为alert允许这个原生中间活动，导致#783/786/788持续OUTCOME_UNKNOWN，阻塞同局后续操作。

## Solution

以精确本次原生休整任务开始及非就绪状态确认“休整已选择”，并允许已观察到的OPERATION活动；不把任务接纳当作生命值恢复，不放宽既有HEAL的无提交判断或其他任务/归属规则。

## User Stories

1. As a player, I want an accepted native heal task confirmed, so that the game can continue.
2. As a player, I want native OPERATION retained during task start, so that the tool mirrors the real engine.
3. As a player, I want task selection separated from later healing, so that HP is never invented.
4. As a maintainer, I want exact native Started/Added identity retained, so that unrelated operations do not confirm healing.
5. As a maintainer, I want world, turn, owner, unit and token retained, so that stale tasks stay unknown.
6. As a player, I want unknown outcomes confirmed without resubmission, so that one heal request remains one submission.
7. As a player, I want an already healthy unit handled as a no-op, so that no needless task is entered.
8. As a player, I want an already healing no-op to require its actual native activity, so that OPERATION is not guessed to be an older heal.
9. As a maintainer, I want ready=true heal requests left unknown, so that automation-specific instant completion rules remain scoped.
10. As a maintainer, I want unreadable readiness/activity left unknown, so that missing evidence is not success.
11. As a player, I want sleep, alert and exploration behavior retained, so that this repair remains scoped.
12. As a player, I want the original T48 checkpoint recreated through faithful replay, so that war damage and strategic decisions remain.
13. As a player, I want subsequent T48→T49 verified in the same run, so that this repair demonstrably unblocks play.

## Implementation Decisions

- Extend the existing unit-task start confirmation for freshly submitted heal only, matching the native behavior already handled for fresh alert: entered, returned, exact started, boolean non-ready and current HEAL or numeric native OPERATION.
- Existing no-op checks retain exact activity and full health checks. Do not relax automate, sleep or unrelated readiness paths. No HP modification, direct activity setter or synthetic event.
- Preserve the existing one-submit owner and no-replay contract. Success describes task start, not HP recovery.
- Parent owns live game and recovery. The original MCP was closed solely for read-only native diagnosis, so explicit same-run T45 restoration and faithful replay to T48 precedes further gameplay. Save T48 before the repaired action.

## Testing Decisions

Use the already approved public GameState/MCP/native Lua fixture seams. Red→green fresh heal with exact Started and OPERATION/non-ready; reject absent start, wrong operation/unit/world/turn/token, ready or unreadable values and unrelated activity. Verify no-op OPERATION remains unknown, full-health no-op remains strict and repeated unknown never resubmits. Retain alert/sleep/automate tests. Parent verifies native heal acceptance and subsequent game actions/T49; no need to force HP gain through a test-only battle or state change.

## Out of Scope

Automatic healing policy, guaranteed HP gain, damage/activity mutation, promotion compatibility path, wider acceptance rules or game policy automation.

## Further Notes

b09-native-heal.txt: tokenfb283d6bbc3d4e61acccce180b9e3a90, unit262146, HEAL hash2126026491/Index13, entered/returned/started=true, expectedACTIVITY_HEAL=-1298237662; actualACTIVITY_OPERATION=1225574625, readyfalse, damage15, moves3, T48. #787 dismissed DiplomacyActionView, #788 original confirm stillunknown. Last checkpointgold_20261019_t045_b08_verified (#731), same run e8b4d6a62dca49bbb256e8264beb18f2. Recovery/replay parent responsibility; worker must not touch game.
