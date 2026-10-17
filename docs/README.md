# 文档导航

Agent 从根目录 [AGENTS.md](../AGENTS.md) 开始；命中任务后读取对应指南，不按目录顺序通读。指南说明如何操作，架构解释实现，规格与验收记录保留当时的范围和证据。

## 当前任务

| 需要完成的工作 | 文档 |
| --- | --- |
| 安装依赖、配置 FireTuner 或 MCP 客户端 | [安装与启动](../README.md#安装与启动) |
| 连接游戏、查询、动作、推进、世界议会 | [游戏操作](agent-playbook.md) |
| 查询研究、建设、经营、军事、外交或胜利规则 | [本机版本知识库](../civ6_knowledge/README.md) |
| 保存、菜单换档、未知结果或完整恢复 | [保存与恢复](agent-recovery.md) |
| 定位代码、离线测试、实机验收或提交 | [开发与验证](development.md) |
| 理解状态所有权、Lua 和通信机制 | [当前架构](architecture.md) |
| 核对唯一基准的设置、身份和指纹 | [基准说明](../fixtures/saves/README.md)及其清单 |

## 规格与验收

仅在定位对应改动或核对成果时读取。历史记录中的提交、测试数量与实机结果只适用于该记录，不代表当前分支已重新验收。

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
