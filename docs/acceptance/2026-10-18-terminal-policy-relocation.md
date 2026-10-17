# 第 106 回合政策跨槽修复验收

原请求 `2=POLICY_CIVIL_PRESTIGE,3=POLICY_NATURAL_PHILOSOPHY` 从 CANNOT_SLOT 变为单次提交成功；全量离线 1,028 项通过，Standards／Spec 均 0 项发现。实现 `2d5dd7e`，集成 `70f1a38`，未 push。

原因是原生 CanSlotPolicy 先拒绝已生效政策，旧工具逐槽验证时没有考虑完整计划将腾出原槽。修复按最终计划验证解锁、过时状态、槽类型、来源腾出与无重复条件，一次 RequestPolicyChanges；回读核对全部槽位，回执异常明确标记未完整验证。

离线使用 GameState＋隔离 Lua 5.1，新增 33 项回归。全量 `1028 passed in 29.55s`，源码／测试／依赖子树与受测内容一致，编译及 diff 检查通过；未新增依赖。

实机先以 `save_game` 保留 `benchmark_turn_106_before_policy_fix_20261018`，经菜单加载该检查点 26.756862 秒，PID 17103 不变；只调用一次修复后的 set_policies，0.091541 秒返回确认。经济槽为民间威望、通配槽为自然哲学；军事槽仍为征兵、棱堡，文明／种子／回合均未变化。另存 `benchmark_turn_106_policy_verified_20261018`，原生完成及 939,203 字节文件确认。

菜单恢复的原生汇总分 166→169，分类向量不变，沿用已确证的原生汇总差异说明。未将汇总分变化作为世界状态变化。

证据：`~/.civ6-mcp/acceptance/20261018-turn-terminal/` 内 `policy-fix/`、`policy-live-validation.json`、两份 turn106 存档清单、`result-saves/` 与两轴审查报告。原始基准、第 100 回合结果及旧验收均保留，实施工作树已清理。

[规格](../specs/2026-10-18-terminal-policy-relocation.md)与[工作项](../specs/2026-10-18-terminal-policy-relocation-tickets.md)。本记录仅关闭政策阻塞的修复验证；整体已到第 244 回合原生终局，见[全程验收](2026-10-18-turn-terminal.md)。
