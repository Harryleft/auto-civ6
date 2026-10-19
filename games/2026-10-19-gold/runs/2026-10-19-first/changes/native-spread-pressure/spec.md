# B23：传教确认误用城市宗教压力读数

Status: resolved

## Problem Statement

T128传教士2686987/type3/religion12在拉格什327684/owner0/34,26执行一次spread_religion，charges3→2、MP0且城市已转拜火；原请求及关闭科技弹窗后的confirm-only均PRESSURE_UNCONFIRMED，无法继续本局。原回执started/entered/returned=true、绑定正确，但使用GetTotalPressureOnCity(12)前后均5.96875，GetReligionsInCity的Religion12.Pressure实际217.90625。工具把与当前累计压力不同的读数用作传播效果，误拒绝真实传播。

## Solution

保持原生传播单次提交与原目标确认，使用原城中精确宗教条目的实际累计Pressure验证增加；不依靠多数转换、不发第二次传播，不修改城市宗教。保留世界/回合、单位、宗教、城市及次数/事件见证。

## User Stories

1. As a player, I want legal native spread confirmed from actual accumulated religion pressure, so that a completed spread does not block continuation.
2. As a maintainer, I want the exact original city owner/fullID/location and religion bound, so that another city's or religion's pressure cannot confirm.
3. As a player, I want unchanged incoming-pressure readings to be irrelevant when accumulated pressure increases, so that the T128 case succeeds.
4. As a maintainer, I want accumulated pressure unchanged or lower to remain unknown even if a different getter increases, so that unrelated observations cannot confirm.
5. As a maintainer, I want unavailable, duplicate, malformed or non-finite religion-pressure facts fail closed, so that uncertainty never becomes a zero baseline or success.
6. As a player, I want a verified complete city-religion list without my religion to represent zero accumulated pressure, so that a first spread to a new city remains possible.
7. As a maintainer, I want exact native operation-start, single charge consumption or exact final-charge removal retained, so that pressure alone cannot confirm.
8. As a player, I want retry/lost-response/cancel confirmation to retain the original intent and never resubmit, so that one spread remains one spread.
9. As a maintainer, I want known unit/religion/city/scope changes latched invalid and incomplete facts unknown, so that restoration of old values cannot release pending improperly.
10. As a maintainer, I want pending mutation/save/end-turn protection retained, so that no dependent play proceeds on an unknown result.
11. As a player, I want the original T128 spread, subsequent turn and checkpoint validated, so that the specific blocker is repaired in this same run.

## Implementation Decisions

- Use the existing unit-effect GameState/ActionLifecycle/native Lua owner and public unit_action spread_religion contract; existing approved test seams only.
- Replace the spread pressure fact with the exact native GetReligionsInCity row's Pressure for the bound religion. Validate the collection and relevant row identities/values; do not use GetTotalPressureOnCity as accumulated-pressure evidence. Native shipped CityBannerManager distinguishes GetReligionsInCity records and pressure direction readings.
- Use the same cumulative-pressure reader before submission and during confirmation. Bind and recheck original actor/religion/city/scope, native qualification, operation event and exactly one charge consumption or final-charge removal. Only current pressure greater than original baseline confirms effect. No majority requirement, synthetic event, direct pressure/religion setter or replay.
- Preserve sticky known scope/identity invalidation, unknown protection, and lost-confirmation evidence through the existing owner. Reuse adjacent prior-art protections narrowly where needed for this spread; do not refactor other unit effects.
- Missing religion means zero only from a successfully read complete valid native collection; duplicate religion rows, invalid data or errors cannot imply absence. Never report invented followers or reward.

## Testing Decisions

Use existing public MCP/GameState/FakeConn/Lua5.1 vertical red→green seam, already approved. Reproduce T128 bound missionary/city/charges and unchanged GetTotalPressureOnCity while row Pressure rises. Cover missing zero baseline, duplicate/malformed/unavailable records, wrong religion/city/actor/location, same/lower cumulative pressure, unchanged/wrong events or charges, final-charge removal, retries/no resend, cancellation/lost submit/lost confirmation and sticky scope gates. Preserve B22/GP neighbors and public save/end-turn protection; no source-text-only critical tests. Parent explicitly restores same confirmed T128 checkpoint after closing unknown owner for pure diagnosis, replays already logged independent T128 choices, measures true pre-pressure and saves ready point; refresh code without world reload, one spread and next-turn/save verified before resolution.

## Out of Scope

Religious combat, conversion strategy automation, rewriting religion queries, other GP/condemn mechanics, broader transport/lifecycle redesign, direct religious effects or favorable outcome reload.

## Further Notes

#2364 original missionary moved34,26; #2365 one spread unknown; #2367 showscharges2; #2369 popup dismissed; #2370 originalconfirm-only remainsunknown; #2371 citynow拜火2/3. Original Lua receipt #2373 token e7a8eff1b4454ff2b477130171739326, actor2686987/type3/religion12/city327684/34,26, T128/seed-1896272000, started/entered/returned=true, old storedpressure5.96875 and currentGetTotalPressure5.96875; nativecityrowPressure217.90625. Original persistent owner was clean-closed for pure diagnosis only; gameplay must not resume before explicit confirmed same-run checkpoint restoration. Lastconfirmed gold_20261019_t128_b22_verified911927bytes/run e8b4d6a62dca49bbb256e8264beb18f2. B22 resolvedfa4aac3.
