# 友好与同盟结果确认验收

规格：[友好声明与同盟结果确认](../changes/2026-10-18-terminal-friendship-confirmation.md)；[工作项](../changes/2026-10-18-terminal-friendship-confirmation-tickets.md)。友好实现 `f8e9fc9`，同盟实现 `6e435e2`，集成 `7bac373`；任务分支 `codex/architecture-deepening`，未 push。

两种请求均只执行一次既有提交序列，再在包含读取耗时的短期限内只读确认。友好须有本方与目标双方的原生 DECLARED_FRIEND 见证；同盟须双方 ALLIED，且原生 GetAllianceType 经 GameInfo.Alliances 映射到请求类型。未确认、矛盾、缺失、超时或丢回执返回 PROPOSED_UNCONFIRMED，不猜测已接受、已拒绝或失败，也不重发；原生前置拒绝保持零提交。

| 验证 | 结果 |
| --- | --- |
| 离线 | 完整 1,279 项通过，30.66 秒；相关 456 项通过，3.18 秒；编译与 diff 检查通过，提交 tree 与实际 tested-tree 一致 |
| 独立双轴 | Standards／Spec 均 0 项发现 |
| 友好未成立实机 | 菜单恢复第 180 回合后，瑞典为 UNFRIENDLY 且允许声明友好；一次新声明耗时 5.0360 秒，返回 PROPOSED_UNCONFIRMED，随后 get_diplomacy 仍为 UNFRIENDLY |
| 友好成立实机 | 主进度第 199 回合巴西友好到期；核对 FRIENDLY／可声明后，一次新声明 0.2547 秒返回 ACCEPTED，随后原生 DECLARED_FRIEND |
| 同盟成立实机 | 加拿大已为 DECLARED_FRIEND，get_trade_options 显示同盟合法；一次 RESEARCH 请求耗时 0.3172 秒，返回 ACCEPTED，随后 get_diplomacy 为 ALLIED／RESEARCH Lv1 |
| 菜单恢复 | 192 → 180 耗时 29.0309 秒，180 → 192 耗时 28.8693 秒；两次均保留 PID 49863，诊断未调用 end_turn，零回合增长，累计诊断重放仍为 13 次 |
| 恢复后读取 | 返回第 192 回合后重新查询 overview、8 个单位及 2 座城市；原生总分 272 → 275，正权重类别向量不变，现有说明保留聚合差异，不据此宣称完整世界状态相等 |

第 199 回合主进度另验证友好成立；未确认与成立的公开语义均已实测。第 180 回合诊断结果另存为 `benchmark_turn_180_diplomacy_confirmation_verified_20261018`（1,173,737 字节）；主进度检查点 `benchmark_turn_192_before_diplomacy_replay_20261018`（1,207,530 字节）已恢复。两档均保留副本、SHA-256 及元数据，原始基准和既有存档不删除。修复实测完成，整体已到第 244 回合原生终局，见[全程验收](2026-10-18-turn-terminal.md)。

证据位于 `~/.civ6-mcp/acceptance/20261018-turn-terminal/`：`friendship-alliance-live-validation.json` 保留两次菜单恢复及第 180 回合新请求；`friendship199-live-validation.json` 记录自然到期后的成功续约；`friendship-alliance-restoration-validation.json` 补充恢复后的新鲜读取与零推进核对；`save-180-diplomacy.json`、`save-192.json` 保留存档元数据。`friendship-fix/` 保留红绿测试、全量日志、原生来源和 `tested-tree-combined.json`；`friendship-review-standards.md`、`friendship-review-spec.md` 保留双轴审查。
