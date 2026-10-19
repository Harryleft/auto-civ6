# B16：本方候选人被伟人池预检查拒绝

Status: pending-review

## Problem Statement

T73已累积82.296875/80科学家点数，原生候选奥马尔·海亚姆133的Claimant=0（本地玩家），CanRecruitPerson与CanRejectPerson均true。公共招募工具却以CANDIDATE_UNAVAILABLE零提交拒绝，无法取得伟人及教育尤里卡，阻塞发展。

## Solution

遵从原生伟人池当前唯一候选与各操作资格，允许具备本操作资格的候选招募；继续拒绝歧义、未知资格和不足资源。原生 Can* 对他方或不可用候选返回 false 时拒绝；不自行赋予 Claimant 资格语义。招募成功仍须确认原始资源变化和新产生的对应伟人单位，不以 Claimant 字段代替招募完成。

## User Stories

1. As a player, I want an eligible local candidate recruitable, so that earned Great Person points can be spent.
2. As a player, I want native eligibility respected, so that timeline assignment is not mistaken for completed recruitment.
3. As a player, I want native ineligibility for other-player candidates respected, so that their recruitment rights remain intact.
4. As a maintainer, I want missing or malformed candidate data refused, so that uncertain state cannot authorize a request.
5. As a player, I want current candidate identity bound, so that another individual cannot be recruited accidentally.
6. As a maintainer, I want recruit, reject and patronize separately checked, so that one operation's permission cannot authorize another.
7. As a player, I want actual native resource and unit evidence, so that a submitted request is not called complete.
8. As a player, I want repeated unknown attempts to confirm only, so that no second purchase or recruitment occurs.
9. As a maintainer, I want scope changes and lost replies kept safe, so that unrelated units cannot confirm the original request.
10. As a player, I want normal unclaimed candidates compatible, so that the repair does not remove existing patronage behavior.
11. As a player, I want this same T73 run continued, so that resources and prior choices are preserved.
12. As a player, I want recruitment, legal activation and a later turn checkpoint validated, so that the repaired tool supports continued play.

## Implementation Decisions

- Read the shipped native Great People UI and actual timeline/Can* facts before finalizing the candidate contract. Keep GameState, ActionLifecycle and native pool request boundaries.
- Correct only the candidate precondition. The shipped `Base/Assets/UI/Popups/GreatPeoplePopup.lua:700–738` reads `GetTimeline()` and each operation's `CanRecruitPerson` / `CanRejectPerson` / `CanPatronizePerson` independently. Its Claimant filter applies only to the past timeline; this does not establish Claimant as current-operation eligibility. Bind the unique current individual and require the requested native Can* result to be exactly true.
- Retain resource baseline, single submit and exact new-unit confirmation. No direct spawn, point or resource setter. Do not broaden this into unrelated Great Person UI or strategic policy changes. Missing or duplicate candidates and false/unknown operation eligibility remain refused; existing pending behavior is preserved.
- No unresolved gameplay mutation exists: the refusal happened before submit. Parent owns live checks; a clean client code refresh does not require world restoration.

## Testing Decisions

Use the previously approved public GameState/MCP/native Lua seams. Red→green the native T73 local Claimant=owner, CanRecruit=true, points above80 scenario; confirm one request, actual80-point spend and exact new133 unit. Check native false for a foreign candidate, false/unknown/malformed operation eligibility, duplicate/missing candidate, invalid class/cost, ordinary unclaimed compatibility, separate reject and gold/faith patronage eligibility, and retained pending/confirmation behavior. Parent validates actual recruitment, legal activation, education boost and subsequent turn/save in the original run.

## Out of Scope

Changing Great Person costs, rewards, game rules, automatic recruitment/activation, generic pool redesign or unrelated gameplay fixes.

## Further Notes

#1332 and #1337 show recruitable133; #1336 refuses CANDIDATE_UNAVAILABLE without submit. #1338 pure read: local0/T73, Individual133/Class5/Era2/Cost80/Claimant0, CanRecruit=true, CanReject=true, points82.296875, activation requires Campus. #1339 saves gold_20261019_t073_before_gp_recruit (796860bytes). Current integration has B15 review repair pending independently. Existing approved verification seams apply; no new seam or user interview needed.
