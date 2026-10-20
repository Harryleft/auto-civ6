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
