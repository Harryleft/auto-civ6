# 文档导航

Agent 从根目录 [AGENTS.md](../AGENTS.md) 开始；命中任务后读取对应指南，不按目录顺序通读。指南说明如何操作，架构解释实现，规格与验收记录保留当时的范围和证据。

## 当前任务

| 需要完成的工作 | 文档 |
| --- | --- |
| 安装依赖、配置 FireTuner 或 MCP 客户端 | [安装与启动](../README.md#安装与启动) |
| 连接游戏、查询、动作、推进、世界议会 | [游戏操作](agent-playbook.md) |
| 安排扩张、胜利路线与对手胜利预警响应 | [获胜决策](agent-strategy.md) |
| 通过 MCP 查询规则、解锁条件或机制 | [知识查询](../civ6_knowledge/README.md#mcp-查询) |
| 保存、菜单换档、未知结果或完整恢复 | [保存与恢复](agent-recovery.md) |
| 定位代码、离线测试、实机验收或提交 | [开发与验证](development.md) |
| 理解状态所有权、Lua 和通信机制 | [当前架构](architecture.md) |
| 核对唯一基准的设置、身份和指纹 | [基准说明](../fixtures/saves/README.md)及其清单 |

## 规格与验收

- [来约零提交与世界失效锁存](specs/2026-10-19-incoming-p2-boundaries.md)与[离线验收](acceptance/2026-10-19-incoming-p2-boundaries.md)：两项 P2、34 个新增回归、341 项相关测试；保留 T233 成果，不实机、不 push。

- [T233 终局修复合入 main](acceptance/2026-10-19-terminal-main-merge.md)：已验证集成树完整合入、存档与胜利证据哈希保全，未 push。

- [T121 至本轮终局验收](acceptance/2026-10-19-turn-terminal.md)：净推进112回合至T233科技胜利，九项修复、菜单恢复及存档保全。
- [贡献未决与信息弹窗](acceptance/2026-10-19-contribution-popup-progress.md)：原生事件暂停、显式关闭通路、157项相关测试和未知门禁保留。

- [交易确认请求身份](specs/2026-10-19-trade-request-identity.md)与[验收](acceptance/2026-10-19-trade-request-identity.md)：旧尝试身份门禁，97项相关测试及实机拒绝／续行。

- [城市宜居度总量](specs/2026-10-19-city-amenities-total.md)与[验收](acceptance/2026-10-19-city-amenities-total.md)：原生总量语义、21项相关测试及同轮对照。

- [原生科研与市政资格](specs/2026-10-19-native-civic-eligibility.md)与[验收](acceptance/2026-10-19-native-civic-eligibility.md)：随机未来树原生候选、UI及既有回退前置门禁，266项相关测试。

- [皇家学会贡献验收](acceptance/2026-10-19-royal-society-contribution.md)：601 项相关测试、单次贡献与原生计数解释、T191／192 消耗及项目确认、保存与菜单恢复。

- [间谍撤离身份与确认验收](acceptance/2026-10-19-spy-escape-identity.md)：696 项相关离线测试、完整 ID、原生离图见证、T148 同路线复验及菜单恢复后续行。

- [原生掠夺验收](acceptance/2026-10-19-native-pillage.md)：580 项相关离线测试、T132 瑞典毛皮营地真实损坏及撤退，继续同轮终局验证。

- [来约试价与反提案验收](acceptance/2026-10-19-incoming-counteroffer.md)：官方原始接口、只评估不成交、世界门禁及同价反提案确认修复、T139 显式成交与续行。

- [城市产出偏好验收](acceptance/2026-10-19-city-yield-focus.md)：487 项相关离线测试、T141 原生偏好与幂等确认、保存及菜单恢复，继续至 T148。

- [第 101～121 回合调度与决策验收](acceptance/2026-10-18-turn121-scheduling.md)：净 20 回合、失去路径队列取消及议会 B 结果修复、相关模块测试、双份结果档与十三类菜单恢复比较；[取消规格](specs/2026-10-18-cancel-unpathable-queue.md)、[议会标签规格](specs/2026-10-18-congress-outcome-label.md)。
- [远程 CI 测试替身修复](acceptance/2026-10-18-ci-pending-dispatch.md)：同步外交未决字段及参数化场景，194 项相关测试通过；修复尚未 push。
- [结束回合世界身份绑定验收](acceptance/2026-10-18-end-turn-world-scope.md)：270 项相关离线回归、异世界及取消后保留门禁、双轴复查与旧成果保护；未实机；[规格](specs/2026-10-18-end-turn-world-scope.md)与[工作项](specs/2026-10-18-end-turn-world-scope-tickets.md)。
- [第 81～101 回合调度验收](acceptance/2026-10-18-turn101-scheduling.md)：异步交易、本地队列与显式取消、相关模块测试、净 20 回合、双份结果档及十类恢复比较；原地治疗／驻守由模型明确判断。
- [第 81～101 回合调度与等待识别规格](specs/2026-10-18-turn101-scheduling.md)与[工作项](specs/2026-10-18-turn101-scheduling-tickets.md)：外交原生关闭、等待阶段、未来移动前置拒绝及资源提示；T81 verified 同轮续测。
- [第 61～81 回合查询与展示验收](acceptance/2026-10-18-turn81-queries.md)：1,805 项离线、净 20 回合、两份结果档与十类恢复比较；保留移动及长等待新增待办。
- [第 61～81 回合查询与展示规格](specs/2026-10-18-turn81-queries.md)与[工作项](specs/2026-10-18-turn81-queries-tickets.md)：商路候选、资源可见性、通知详情；本轮 T61 检查点续测。
- [第 41～61 回合修复验收](acceptance/2026-10-18-turn61-readiness.md)：1,750 项离线、净 20 回合、第四城、原生待命复现、结果存档与双次菜单恢复。
- [第 41～61 回合跳过、待命与叙述修复规格](specs/2026-10-18-turn61-readiness.md)与[工作项](specs/2026-10-18-turn61-readiness-tickets.md)：从本轮已验证第 41 回合检查点续测，保留已有成果。
- [第 21～41 回合修复验收](acceptance/2026-10-18-decision-repair-turn41.md)：1,719 项离线、净 20 回合、第三城、资金补修与菜单恢复；保留结果档及新增待办。
- [第 21～41 回合决策与调度修复规格](specs/2026-10-18-decision-repair-turn41.md)与[工作项](specs/2026-10-18-decision-repair-turn41-tickets.md)：近战执行资格、使团确认、专属改良、市政进度；本轮第 21 回合检查点续测。

仅在定位对应改动或核对成果时读取。历史记录中的提交、测试数量与实机结果只适用于该记录，不代表当前分支已重新验收。

- [文明特性与自主决策离线验收](acceptance/2026-10-18-civilization-knowledge.md)：文明／领袖能力与公开身份、三类信息决策指南、1,517 项离线和审查修复；未实机；[规格](specs/2026-10-18-civilization-knowledge.md)。
- [世界知识 MCP 验收](acceptance/2026-10-18-knowledge-mcp.md)：1,472 项离线与双轴审查、main 合并、当前会话工具发现及搜索／三层读取 4 次直接调用；未连接游戏；[规格](specs/2026-10-18-knowledge-mcp.md)。
- [获胜策略离线验收](acceptance/2026-10-18-win-strategy.md)：扩张与胜利路线指南、原生事实和确认后预警，1,448 项离线；新局按用户要求暂缓；[规格](specs/2026-10-18-win-strategy.md)。
- [第 100 回合至原生终局验收](acceptance/2026-10-18-turn-terminal.md)：净推进 144 回合至第 244 回合失败，瑞典科技胜利；八组修复、1,385 项离线及存档保护。
- [终局类型与保存边界验收](acceptance/2026-10-18-terminal-outcome-boundary.md)：官方胜利类型、终局新保存零提交与原生结果复验；[规格](specs/2026-10-18-terminal-outcome-boundary.md)。
- [第 207 回合移动目标保护验收](acceptance/2026-10-18-terminal-move-target.md)：1,340 项离线、初版实测不足与原生路径保护、拒绝后合法移动；[规格](specs/2026-10-18-terminal-move-target.md)。
- [友好与同盟结果确认验收](acceptance/2026-10-18-terminal-friendship-confirmation.md)：1,279 项离线、友好未确认与研究同盟成立实测、菜单诊断恢复；[规格](specs/2026-10-18-terminal-friendship-confirmation.md)。
- [第 167 回合议会提案验收](acceptance/2026-10-18-terminal-congress-proposals.md)：1,185 项离线、世界博览会与 3 决议显式混合投票、中文修复及会后确认指引；[规格](specs/2026-10-18-terminal-congress-proposals.md)。
- [外交当前上下文验收](acceptance/2026-10-18-terminal-diplomacy-context.md)：1,119 项离线、NONE／INCOMING／DIALOGUE 实机复验、错误目标拦截及显式 EXIT；[规格](specs/2026-10-18-terminal-diplomacy-context.md)。
- [第 117 回合和平确认修复](acceptance/2026-10-18-terminal-peace-confirmation.md)：1,081 项离线、原场景单次提议及延迟成立回读；[规格](specs/2026-10-18-terminal-peace-confirmation.md)。
- [第 113 回合攻击类别修复](acceptance/2026-10-18-terminal-attack-mode.md)：1,062 项离线、拒绝不移动及实际远程攻击复验；[规格](specs/2026-10-18-terminal-attack-mode.md)。
- [第 106 回合政策跨槽修复](acceptance/2026-10-18-terminal-policy-relocation.md)：完整换卡计划、1,028 项离线及菜单恢复实测；[规格](specs/2026-10-18-terminal-policy-relocation.md)。
- [五主题知识扩展规格](specs/2026-10-17-knowledge-expansion.md)与[工作项](specs/2026-10-17-knowledge-expansion-tickets.md)：经营、资源、军事、外交、胜利宗教；[离线验收](acceptance/2026-10-17-knowledge-expansion.md)记录九主题 1,004 卡、995 项测试及来源关联修复。
- [本机世界知识规格](specs/2026-10-17-civ6-knowledge.md)与[工作项](specs/2026-10-17-civ6-knowledge-tickets.md)：独立版本知识、四主题及离线检索；[离线验收](acceptance/2026-10-17-civ6-knowledge.md)记录 195 张卡、957 项测试和审查修复。
- [四项架构深化规格](specs/2026-10-17-architecture-deepening.md)与[工作项](specs/2026-10-17-architecture-deepening-tickets.md)：动作确认、存档替换、回合推进和议会事实解释的状态归属；[离线验收](acceptance/2026-10-17-architecture-deepening.md)记录 933 项测试及双轴审查修复结果。
- [第50至100回合规格](specs/2026-10-17-turn-100-scheduling.md)与[验收](acceptance/2026-10-17-turn-100-scheduling.md)：871项离线测试、议会完整投票、移动／动作来源／城市射击确认、阶段存档及最终菜单恢复。
- [菜单换档规格](specs/2026-10-17-menu-save-reload.md)与[耗时验收](acceptance/2026-10-17-menu-save-reload.md)：735项离线测试、进程保留、两组配对耗时、状态清理及新存档恢复。
- [灾害弹窗、恢复读取与分数差异验收](acceptance/2026-10-17-reliability-repair.md)：两处灾害回放、分数原因、保存恢复及菜单换档分析。
- [调度修复与第 50 回合验收](acceptance/2026-10-17-scheduling-repair-turn-50.md)：630 项离线测试、20 净推进、最终保存恢复及新增问题。
- [第 21～30 回合调度问题](acceptance/2026-10-17-turn-30-scheduling.md)：当时的 3 次未知及恢复、完成事件与响应归属问题证据。
- [第 30～50 回合调度修复规格](specs/2026-10-17-scheduling-repair.md)：响应归属、纯查询、真实完成、区域落位、移动与战斗估算；[工作项](specs/2026-10-17-scheduling-repair-tickets.md)。
- [修复与 20 净回合复验](acceptance/2026-10-17-confirmation.md)：575 项离线测试、完整重新推进、最终保存恢复及证据。
- [动作确认与恢复时序修复规格](specs/2026-10-17-action-confirmation.md)：20 回合审查问题、实施约束和验收条件。

## 专项参考

- 分析存档格式时读[存档格式研究](save-file-format.md)。
- 配置 macOS Aspyr 启动器时读[启动器说明](research/bypassing_aspyr_launcher.md)。
- 安装随附基准或分析旧日志时读[存档与历史数据](../fixtures/README.md)。
- 调查旧设计时才进入[历史归档](archive/README.md)；归档不作为当前运行指令。
