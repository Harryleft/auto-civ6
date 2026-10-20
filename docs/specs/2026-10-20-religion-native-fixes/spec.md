# 宗教防御实机接口修复

Label: ready-for-agent
Integration: codex/gold-terminal
Origin: 用户授权重载服务并用原局已确认检查点完成实机验收，不开新局。原终局证据保持独立。

## Problem Statement

T139／T141真实检查点暴露离线替身未覆盖的两项问题：宗教防御查询的控制字符过滤破坏中文UTF-8；使徒被旧FormationClass推断拒绝进入有建造者的城市，尽管当前原生移动资格true、路径完整且本回合可达。

## Solution

保留原生中文名字，准确清理协议分隔符与ASCII控制字符；宗教单位移动尊重原生层与资格，不能仅用共享civilian formation推断同层堆叠冲突。原动作所有者和未知不重发规则保持。

## User Stories

1. As a player, I want 乌鲁克 and other native UTF-8 names preserved, so that religious reports identify cities correctly.
2. As a player, I want protocol separators and ASCII control bytes sanitized without corrupting non-ASCII bytes, so that reports remain readable and parseable.
3. As a player, I want a religious unit allowed to share a native-compatible plot with a builder or other civilian, so that I can reach cities for defense.
4. As a player, I want real same-layer conflicts and unknown legality rejected before submission, so that a narrow fix does not bypass stacking rules.
5. As a player, I want native visibility, diplomatic access, movement/path checks and exact witnesses preserved, so that movement cannot silently become an attack or multi-turn completion.
6. As a player, I want lost replies or changed identity to keep the existing pending owner, so that no move is repeated.
7. As a maintainer, I want the observed native cases reproduced through public MCP/GameState with executed Lua, so that replacing the native engine in tests does not hide these defects again.
8. As a tester, I want the same original T139 world and exact entities used after refresh, so that native acceptance proves the repair.

## Implementation Decisions

- Reuse the existing integration branch, local Markdown tracker and public MCP/GameState/executed-Lua seams already approved by the user. No additional seam or permission flow.
- Only sanitize defined ASCII control bytes and pipe delimiters in the religious observation wire format. Do not apply locale-sensitive byte classes to UTF-8 text.
- Use first-party native layer facts where available, or a narrowly justified religious/nonreligious compatibility decision grounded in actual native qualification and path. Unknown layer/legality must not become compatibility; do not guess a new engine API or layer integer.
- Preserve native CanStartOperation, real path checks, fog/peace boundaries and move action confirmation; actual same-layer conflicts remain refused. Do not rewrite the movement lifecycle or broaden this to auto defense.
- Worker owns each independent ticket in an isolated worktree, merger integrates, independent Spec/Standards reviews run on fixed refs, then parent repeats original live case.

## Testing Decisions

- Q01 native evidence: at T139, Locale.Lookup returns 乌鲁克, bytes228,185,140,233,178,129,229,133,139. Old wire string:gsub('%c',' ') replaces byte129 and yields 乌� 克. Public query must preserve exact text and still escape pipe/newline/ASCII controls. Exercise real Lua with native locale behavior if available; record limitations rather than mock a tautological string replacement.
- M01 native evidence: public move to35,22 refused STACKING_CONFLICT for builder3211267; after moving apostle3145737 to35,24, native CanStartOperation(MOVE_TO,35,22)=true and GetMoveToPathEx plots1475→1415→1355 all turns1. Builder is still35,22. Compare religious vs nonreligious layer using actual native evidence; existing military/civilian rejection and native unknown/path/hostility tests remain.
- RED→GREEN at existing high public seam; actual generated Lua executes, no private-method source assertions. Run focused and directly related suites, compile/diff checks, dual reviews.
- Native replay uses gold_val_20261020_t139_query_move_ready (929031bytes), same run e8b4d6a62dca49bbb256e8264beb18f2. Check current actor/target readiness before retry. No direct setters, synthetic events or unlogged commands.

## Out of Scope

New game/baseline, original-terminal changes, game setters, broad movement refactor, hidden-unit disclosure, automatic retries/recovery, background scanning, push.

## Further Notes

Validation evidence is separate under original run religion-native-validation-2026-10-20. The user has explicitly authorized same-run checkpoint recovery and native actions for acceptance. Native no-moves refusal at T141 was legitimate and consumed no charge; do not falsely classify it as an implementation bug.

## C01 Native combat completion without UnitOperationStarted

T140 public religious_attack #38 entered and returned once, actual CombatVisEnd matched the exact attacker/defender, target5570570 had exact UnitRemovedFromMap plus absence from both native lookups, actor3145737 HP100→86 and position35,22→36,23, war remainedfalse. Read-only receipt diagnostic records started=nil, combat=true, invalid=nil. This completed theological kill has no matched generic movement-start witness; its exact native omission or argument-shape cause is unproven; requiring it leaves a legitimate combat permanently pending.

9. As a player, I want exact native theological combat and HP/removal proof accepted when the engine omits the generic operation-start event, so that a completed defense does not block the session.
10. As a player, I want missing actual combat participation, wrong identities, wrong operation events, stale scope, ambiguous HP/removal or lost request return to remain unknown, so that removing the redundant witness does not turn a move or disappearance into combat success.

Implementation decision: retain one native request and its returned flag, exact current-scope combat-participant event, independent HP/removal effects and identity/path latches. Generic UnitOperationStarted is supplementary: any observed wrong actor operation still invalidates, but absence alone cannot disprove the exact native theological battle. Do not fabricate events or relax CombatVisEnd/removal/dual-lookup proof. Preserve unresolved original request; closing its owner does not permit gameplay. Parent explicitly restores the pre-request confirmed T140 checkpoint before replay on fresh service.

Test C01 at existing public MCP/GameState/executed-Lua seam with native transcript's missing start + exact combat/removal/HP, and paired negative missing combat/effect, mismatched operations/identity/scope, entered-throw, unknown/lost-reply. Live replay only from gold_val_20261020_t140_peace_combat_ready (929153bytes), same run, one public attack and independent readback/save. Original #38 remains recorded unknown; replay receives new IDs, no retcon.

## P01 Native self-city purge target

T142#95 Inquisitor3276804 entered Eridu35,22; #96 proves moves2/charges3, strict CanStartOperation REMOVE_HERESY(nil,false,NO_TARGETS)=true, exact owned City131073 at35,22 and foreign religion8 stock406.8515625. GetOperationTargets returns a table with ADDITIONAL_DESCRIPTION only (key-1554772595, predicts10 followers), no PLOTS/MODIFIERS. #97 correctly zero-submits but cannot execute this legitimate action. #98 saves gold_val_20261020_t142_purge_ready (939896bytes), no pending owner.

11. As a player, I want a native-qualified inquisitor standing exactly on an owned city center to bind that exact city even when the generic self-action supplies descriptions but no coordinate targets, so that legal cleansing works.
12. As a player, I want off-center, ambiguous, contradictory or partially unknown native target facts refused without submission, so that the change does not guess a nearby target.

Implementation decision: align the exact city-center case with the existing spread-religion prior art: CityManager.GetCityAt(actor.x,actor.y) must return the same owned city center and stable scalar identity, actor/location/religion/charge and native strict qualification must still match immediately before the single no-parameter RequestOperation. When actual native target coordinates are present, they must identify the same unique owned city and cannot be ignored in favor of the center; malformed/partial contradictory targets remain refused. Off-center operation continues requiring unique actual native target plots. No nearest-city, owner-of-territory or localized-tooltip inference. Existing charge/pressure/removal/unknown and scope protections remain intact. P01 supersedes the earlier blanket requirement for returned PLOTS in the exact self-city case only.

Tests use public MCP/GameState/executed Lua with the native description-only target transcript, positive exact own center and negative foreign/off-center/moved actor/changed or unreadable city/contradictory targets/native false or unknown/last charge and lost replies. Parent repeats actual T142 current exact scene after fresh service, checks pressure and charge, then validates final charge and continuation/save. No direct engine operation bypass.

## Completion

Q01/M01/C01/P01全部resolved；集成生产代码ec9824a，两轴最终无实质问题。T139查询/移动、T140和平神学战、T142普通清除、T144最后charge及后续动作/保存原生通过，原终局7项指纹不变。详细结果随原轮次独立归档。
