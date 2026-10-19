# B07：政体选择后确认原生考虑提醒

Status: ready-for-agent

## Problem Statement

T42，change_government已确认古典共和，完整政策也确认，但ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE持续存在；普通信息弹窗关闭后仍不能结束回合。原生GovernmentScreen会独立设置GovernmentChangeConsidered，当前工具只执行政体变更而未确认该提醒。

## Solution

显式选择政体时补齐原生政府界面对应的考虑确认；保留真实政体采用／无政府状态确认，不以通知消失代替政府采用。已选择同一政体的显式调用也能确认考虑，允许当前原局继续。

## User Stories

1. As a player, I want a confirmed government choice to clear its native consideration blocker, so that the next turn can proceed.
2. As a player, I want explicit selection of my current government to acknowledge consideration, so that I can keep a deliberate choice.
3. As a maintainer, I want the shipped UI acknowledgment used, so that runtime behavior matches normal government review.
4. As a player, I want adopted government and anarchy facts confirmed independently, so that acknowledgment cannot invent a change.
5. As a player, I want acknowledgment submission and confirmation kept distinct, so that native read errors remain unknown.
6. As a player, I want lost responses confirmed without repeating government changes or acknowledgment, so that requests remain single-submit.
7. As a maintainer, I want unrelated policy, civic and dedication operations unaffected, so that the fix stays scoped.
8. As a player, I want unrelated notifications retained, so that decisions are not silently dismissed.
9. As a maintainer, I want missing native acknowledgment APIs refused before change submission where possible, so that unsupported contexts are explicit.
10. As a player, I want the existing T42 checkpoint advanced to T43 and saved after verification, so that the original run continues.

## Implementation Decisions

- Use the same Culture.SetGovernmentChangeConsidered(true) acknowledgment as the native GovernmentScreen. Scope it to explicit named government selection, including deliberate same-government no-op, not pure read queries or generic popup cleanup.
- Verify native GovernmentChangeConsidered after acknowledgment while retaining all current adopted-government and accepted-anarchy result contracts. Existing request receipts/no-replay behavior remain; no generic notification deletion, gate reset or forced end-turn.
- Missing/failed/unknown acknowledgment must not falsely claim a complete tool result. Preserve government facts independently and ensure retries only confirm the original request.
- Parent owns live state; the current confirmedT42 game remains unchanged while the MCP server refreshes. No world restart is needed.

## Testing Decisions

Same already-approved public GameState/MCP/Lua test seam. Red→green T42 adopted-target but considered=false case; explicit same-government acknowledgment; unavailable/failing API, lost response/no replay, target-unconfirmed, unrelated notifications and existing anarchy/research/dedication regression cases as relevant. Live change_government to the already-selected Classical Republic, considered readback, T42→T43 and save. No new seam.

## Out of Scope

Changing rules/resources, arbitrary acknowledgment from a pure query, automatic government selection, broad notification removal or turn-proof weakening.

## Further Notes

#607 government changed toClassicalRepublic, #613 complete policy plan. #616 and#618 end_turn refuseConsiderGovernmentChange even after #617 popup dismissal. #619 notification persists; #620 saved gold_20261019_t042_before_b07. #621 native current government3. Shipped Base UI/Screens/GovernmentScreen.lua line629 calls SetGovernmentChangeConsidered(true), LaunchBar.lua69 checks it. Current native government/policies confirmed and no end-turn request was submitted; safe to refresh MCP code without replacing world.
