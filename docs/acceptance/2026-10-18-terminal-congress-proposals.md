# 第 167 回合议会提案验收

规格：[显式提案投票](../specs/2026-10-18-terminal-congress-proposals.md)；[工作项](../specs/2026-10-18-terminal-congress-proposals-tickets.md)。实现 `5c1d872`，集成 `ecfb0b7`；中文及会后指引增量 `c9850fc`，集成 `909a867`。任务分支 `codex/architecture-deepening`，未 push。

查询、打开与提交共用原生提案资格。一个显式票单覆盖当期决议与提案；统一预算先预留全部首票费用，再按输入顺序分配额外票，随后按决议、提案、提交、继续的顺序发送。缺项、未知费用、非法方向及不明身份在发送前拒绝；部分发送或丢响应不重放。

| 验证 | 结果 |
| --- | --- |
| 完整离线 | 主实现 1,182 项通过，30.13 秒；增量 1,185 项通过，28.81 秒；编译与 diff 检查通过 |
| 独立双轴 | 主实现及增量的 Standards／Spec 均 0 项发现 |
| 原生议程 | 3 项决议及世界博览会；提案 key 为 `WC_EMERGENCY_WORLD_FAIR_-1_-1`，Sender／Target 均为原生 `-1`，可选 Type 缺失；类别 `-2146097697`，限制 `369306947`，赞成／反对均允许 |
| 显式混合投票 | 原生累计费用为 0、6、18、36，上限 4 票；四项分别请求 1／1／4／1 票，只调用一次 queue，报告花费 36／40 支持度 |
| 原请求确认 | 原 `end_turn` 完成 167 → 168；收到「赢得外交胜利决议」「积分竞赛已开始」及外交胜利点数通知 |
| 中文修复 | 原生 `%c` 随 locale 错误替换 UTF-8 延续字节；新函数只清理 ASCII 分隔／控制字节。实际生成函数的实机纯读验证 `UTF8_MATCH=true`、`ASCII_MATCH=true` |
| 会后指引修复 | 旧现场曾将 native NONE／local FINISHED／TurnsLeft=0 误述为新会议。增量的真实 Lua 查询 → 叙述回归覆盖 FINISHED／CONTINUE_REQUESTED，仅提示确认原 `end_turn`；该新指引未在原第 167 回合窗口重读 |
| 文件保护 | 保护清单 724 项无变化；原始基准、既有存档与验收结果保留 |

旧代码在第 167 回合预验证失败，未发送投票。菜单恢复被未决推进门禁拒绝后，使用显式故障恢复加载本轮第 160 回合检查点，耗时 62.8128 秒；重放到 167 的 7 次回合增长另计，累计诊断重放 13 次，不计入净推进。

投票前保存 `benchmark_turn_167_before_congress_proposals_20261018`（1,141,752 字节），完成后保存 `benchmark_turn_168_congress_proposals_verified_20261018`（1,142,475 字节）；两档均核对 sumeria／seed 719665280 及对应回合，保留副本与 SHA-256。本修复实测完成，整体终局续测仍在进行。

证据位于 `~/.civ6-mcp/acceptance/20261018-turn-terminal/`：`congress167-live-validation.json` 保留现场查询、票单、回应和原推进确认；`congress167-display-validation.json` 提取原／坏／新中文纯读结果及 7 次重放；`congress167-recovery.json`、`save-167.json`、`save-168.json`、`preservation-through167.json` 保留恢复、文件与保护证据。`congress-proposals-fix/` 保留红绿测试、两次全量日志及 tested-tree；`congress-proposals-review-standards.md`、`congress-proposals-review-spec.md` 保留双轴审查。

第 192 回合继续读取“世界运动会”：名称、说明与限制中文完整；三项决议与竞赛的混合计划显式请求 2／2／6／1 票，统一分配 102／102 支持度，原请求确认 192 → 193，通知积分竞赛已开始。会后查询已读到下一次会议 24 回合，未再次投票；该现场不冒充 FINISHED／零倒计时窗口的额外覆盖。见 `congress192-live-validation.json`。
