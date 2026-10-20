# C01 神学战斗已完成但通用Started事件缺失

Status: resolved
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Own religious_combat.py confirmation and focused public actual-Lua tests only. Native #38 plus evidence/t140-combat-receipt-diagnostic.json proves exact battle/removal/HP with started=nil, so genericStarted cannot be mandatory completion witness. Keep returned request+exactCombatVisEnd+independentHP/removal/identity/scope/path and optional wrongoperation invalidation; never accept movement/preview alone. No game connection. Read tdd/implement-spec, isolated worktree basedintegration, preserve other edits, commit only ticket and mergeintegrationtip. Parent owns native restore/replay/status.

## Resolution

a2be8bf；226相关检查，两轴通过。旧#38保留unknown，#43显式恢复战前检查点；T140#47／49和平战斗实际100→86／54→0并双查缺席，#50保存通过。未匹配Started的具体引擎原因未证明。

集成生产代码ec9824a；最终10相关套件710通过。父代理原生验收与所有新请求／检查点见[实机验收](../../../../games/2026-10-19-gold/runs/2026-10-19-first/religion-native-validation-2026-10-20/acceptance.md)。
