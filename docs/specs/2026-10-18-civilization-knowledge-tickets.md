# 文明特性知识工作项

规格：[文明特性知识与自主决策](2026-10-18-civilization-knowledge.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 `codex/civilization-knowledge`。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| C1 文明知识与 MCP 主题 | 无 | civ6_knowledge 全目录、knowledge.py、server 知识说明、知识相关测试；本机只读来源与构建 | completed |
| C2 本方和可见对手稳定身份 | 无 | 现有概览／外交的 Lua、模型、narrate 及对应公开测试；不改知识与文档 | completed |
| C3 三类信息决策指南 | 无 | AGENTS、docs/agent-strategy.md、docs/agent-playbook.md、根 README；不改知识与源码 | completed |
| C4 集成审查与离线验收 | C1、C2、C3、C5 | 父任务协调独立 merger、全量离线、双轴审查、验收／索引／tracker 与工作树清理 | completed |
| C5 审查修复：特色单位能力链 | C1、C2、C3 | 单一修复实施者：TypeTags／UnitAbilities／效果条件，缓存与安装两路径及公开回归 | completed |

代码使用此前确认的公开边界按 tdd 实施；单项独立 worktree，交付前同步集成分支。只读本机来源文件不构成游戏连接；所有任务不连接 FireTuner、不调用游戏工具、不改存档、不 push、不合并 main。

## 完成记录

C1 合并 `c688ae7`，C2 合并 `4956f75`，C3 合并 `f9a4551`。初轮全量 1,511 项离线通过；Standards 0 项，Spec 1 项 P2：文明特色单位未继续关联标签、单位能力及效果条件。C5 已合入 `58a10f0`，47 项相关回归及正式资料验证通过；修复后全量 1,517 项离线通过（86.02 秒），两轴复核 0 项未解决问题。原始审查与测试证据保留在本轮验收目录。

C4 已完成构建校验、真实内存 MCP 协议、文档契约核对和成果保护，源码／测试／知识／依赖树与全量测试记录一致。[验收与证据](../acceptance/2026-10-18-civilization-knowledge.md)。实施工作树在确认合入后清理，保留分支与外部证据。未实机、未合并 main、未 push。
