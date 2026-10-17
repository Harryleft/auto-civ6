# 第 117 回合和平确认修复

规格：[和平异步回读](../specs/2026-10-18-terminal-peace-confirmation.md)。实现 `7d4c6f3`，集成 `2d7a19c`；当前任务分支 `codex/architecture-deepening`，未 push。

旧方法提交后只读一次战争状态，瞬时 AT_WAR 被错误解释为 REJECTED；同回合稍后查询已停战。现在仍只提交一次，在现有确认期限内只读查询；唯一精确的 AT_PEACE 才报告 ACCEPTED，其余返回 PROPOSED_UNCONFIRMED，要求先查询外交事实，不自动重发。未新增服务或状态机。

| 验证 | 结果 |
| --- | --- |
| 公开 GameState／FakeConn | 22 项和平用例：立即／延迟、持续战争、无效／冲突回读、读取异常与挂起、前置拒绝；每次最多一次提议 |
| 全量离线 | 1,081 passed，29.78 秒；编译与 diff 检查通过 |
| 独立双轴 | Standards／Spec 各 0 项发现 |
| 菜单恢复原场景 | 第 113 回合检查点恢复 26.66 秒，进程 62702 保持；诊断重放 4 次回合增长至 117 |
| 实机提议 | 提交前明确 WAR；一次 propose_peace 耗时 0.249 秒返回 ACCEPTED；随后的外交查询为 UNFRIENDLY、无战争标志 |
| 保存 | 提议前 `benchmark_turn_117_before_peace_verified_20261018`，970,312 字节；成立后 `benchmark_turn_117_peace_confirmation_verified_20261018`，970,500 字节 |

两档均核对 sumeria／seed 719665280／turn 117，另保留副本及 SHA-256。第 122 回合主续测进度在诊断前已独立保存；诊断重放不计入净推进。实机验证成立分支，未确认及异常分支由隔离测试覆盖。

证据：`~/.civ6-mcp/acceptance/20261018-turn-terminal/` 中 `peace117-misreport.json`、`peace-fix/`、`peace-review-*.md`、`save-117-peace.json` 与 `session.jsonl`。整体已到第 244 回合原生终局，见[全程验收](2026-10-18-turn-terminal.md)。
