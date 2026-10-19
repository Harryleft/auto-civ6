# B19：总督派驻缺少目标城市归属

Status: resolved

## Problem Statement

T91已任命阿玛妮，拟派南马都尔提高文化。原生CanAssignGovernor对owner5/city65536/40,6=true；当前assign_governor(city_id)将目标owner固定本方0，同ID65536实际是乌鲁克40,25，不能表达所选城邦，存在误派风险。未发送错误目标。

## Solution

显式指定并验证目标城市归属，保持默认本方调用兼容。原生规则继续决定该总督能否派驻该城市。

## User Stories

1. As a player, I want an explicit target owner, so that Amani can serve a city-state.
2. As a player, I want own-city calls unchanged, so that existing orders remain compatible.
3. As a player, I want colliding city IDs separated by owner, so that no other city receives my governor.
4. As a maintainer, I want native CanAssignGovernor enforced, so that unsupported foreign postings are refused without submit.
5. As a maintainer, I want actual city owner/fullID/coordinates validated, so that stale or mismatched targets do not confirm.
6. As a player, I want governor assignment confirmed separately from establishment, so that future envoy effects are not invented.
7. As a maintainer, I want request and receipt identities to include target owner, so that confirm-only cannot silently change intent.
8. As a player, I want unknown outcomes retained with one submit, so that retry cannot move a governor twice.
9. As a maintainer, I want unavailable or invalid owner/city facts refused safely, so that malformed input cannot choose a default target.
10. As a player, I want no-op detection bound to target owner and ID, so that same-ID own/foreign cities are not conflated.
11. As a maintainer, I want public MCP/GameState/native Lua tests, so that schema and forwarding match execution.
12. As a player, I want original-run T91 assignment and later checkpoint, so that the chosen strategy can resume.

## Implementation Decisions

- Extend the existing governor assignment public interface with optional target city owner, defaulting to the local owner only when omitted; explicit invalid values must not fall back. Forward it through existing GameState/lifecycle into request/confirmation.
- Bind player initiating operation separately from target owner. Native ASSIGN_GOVERNOR destination player parameter receives the verified target owner. Use native CanAssignGovernor without invented governor/city-state eligibility.
- Confirm exact assigned owner/fullID/coordinates and original target identity. Retain single-submit and confirm-only/lost-response behavior. No other governor operation redesign.
- Read or document the city-state target owner/city identity through existing supported query interfaces as needed to make the call reviewable; avoid unrelated query redesign.

## Testing Decisions

Use existing approved MCP/GameState/FakeConn/native Lua seams. Red→green targetowner5/city65536 vs ownowner0/samecityID; explicit target forwarding, native operation args, no-op collision, changed receipt/assigned owner/fullID/coords, missingcity/native refusal/invalidowners, confirm-only and lostresponse one submit. Preserve own-city default and appoint/promote behavior. Parent verifies T91 native assignment5/65536, later establishment when available, subsequent turn/checkpoint.

## Out of Scope

Governor policy selection, automatic envoy placement, artificial establishment, generic lifecycle refactors, unrelated governor redesign, UI bypass.

## Further Notes

#1711 appointedAmani; #1712 targetowner5/city65536/40,6; #1714 nativeCanAssign=true/currentunassigned. Main native owncapital65536/40,25, source lookup always cityFor(owner,city_id). No incorrect assignment submitted. Same run e8b4d6a62dca49bbb256e8264beb18f2, Sumeria/seed-1896272000/T91, checkpointgold_20261019_t091_b18_verified.
