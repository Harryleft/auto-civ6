# B14：区域顾问遗漏合法水地块

Status: ready-for-agent

## Problem Statement

T66–T68规划尼普尔港口时，get_city_production列出港口可生产，而get_district_advisor始终报告没有合法地块。T68原生只读GetOperationTargets证明PLOTS中包含1776=(36,29)、water=true；顾问统一排除IsWater，导致已有港口相邻加成分支不可达。

## Solution

顾问保留本次原生PLOTS明确给出的合法水地块，列出坐标和已有相邻加成估计，供玩家选择港口位置；仍不把BLOCKED_PLOTS或未返回的格子作为候选，不替玩家放置区域。

## User Stories

1. As a player, I want native legal harbor tiles listed, so that I can plan my coastal city.
2. As a player, I want the same native target set used for land and water districts, so that the query matches actual eligibility.
3. As a player, I want coordinates and existing adjacency estimates retained, so that I can compare options.
4. As a maintainer, I want BLOCKED_PLOTS excluded, so that unavailable tiles are never offered.
5. As a maintainer, I want unrelated target arrays excluded, so that only executable targets are listed.
6. As a player, I want existing land-district results retained, so that this fix remains compatible.
7. As a maintainer, I want unknown/missing targets handled without guessed candidates, so that missing data is not legality proof.
8. As a player, I want advisor queries to remain read-only, so that inspecting candidates never starts construction or spends gold.
9. As a player, I want actual harbor placement verified at the reported coordinate, so that the repair unblocks the same run.
10. As a player, I want T68→T69 verified and saved, so that the blocker has a real turn validation result.

## Implementation Decisions

- Preserve the existing city/build eligibility and native operation-target query. Remove the blanket land-only exclusion for authoritative PLOTS; no enumerating hypothetical water tiles or bypassing native legality.
- Preserve existing adjacency estimator and narration. This repair does not redesign all adjacency formulas. Unsupported estimate scope should not be expanded speculatively.
- Queries remain pure; placement stays in the separate tracked production action with its own native legality and confirmation.
- Parent owns game. Current world T68 has no pending action/turn; confirmed checkpoint gold_20261019_t068_before_harbor_advisor. Clean code refresh is sufficient, no world recovery or replay needed.

## Testing Decisions

Use existing approved public GameState/MCP/native Lua query fixtures, red→green a harbor PLOTS water coordinate that is currently filtered away. Confirm BLOCKED_PLOTS and unrelated arrays remain excluded, native land candidates remain unchanged, missing/empty/invalid targets do not fabricate candidates, and query performs no placement. Parent verifies native harbor candidate then tracked construction at (36,29), T69 and same-run checkpoint.

## Out of Scope

Global adjacency redesign, purchasing tiles automatically, choosing districts in runtime, city strategy automation or relaxing native placement guards.

## Further Notes

#1186/#1218 harbor advisor no-valid result; T68 #1227 pure native: PLOTS_KEY=1933683541; TARGET|1933683541|1776|36,29|water=true. Other array745888826 includes1659=(39,27) mountain and is not eligible. #1226 save782132bytes, CIVILIZATION_SUMERIA/-1896272000/T68/player0, run e8b4d6a62dca49bbb256e8264beb18f2. Earlier E01 orchestration recovery completed #1190–1210 with exact prior Congress votes and T66 decisions; no new game outcome selected.
