# Q01 保留原生UTF-8城市名

Status: resolved
Label: ready-for-agent
Dependencies: none
Spec: ../spec.md

Own religion_defense.py wire sanitizer and focused public query tests only. Native live#7/#14 corrupt city text; #20 proves source bytes and locale-sensitive %c. Keep pressure/schema behavior. Do not change movement or call game.

Read implement-spec/tdd. You are not alone in the codebase; preserve others edits. Confirm isolated worktree base, TDD public MCP/GameState/executed Lua, commit only ticket, merge integration tip before reporting. Parent owns live world/issue states; no reset/rebase/amend/push.

## Resolution

2910c63；Q01真实UTF-8 locale RED→GREEN，248相关检查。T139#23原生查询正确显示乌鲁克；最终#159仍正确。双轴无剩余实质问题。

集成生产代码ec9824a；最终10相关套件710通过。父代理原生验收与所有新请求／检查点见[实机验收](../../../../games/2026-10-19-gold/runs/2026-10-19-first/religion-native-validation-2026-10-20/acceptance.md)。
