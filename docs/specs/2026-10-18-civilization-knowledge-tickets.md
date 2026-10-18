# 文明特性知识工作项

规格：[文明特性知识与自主决策](2026-10-18-civilization-knowledge.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 `codex/civilization-knowledge`。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| C1 文明知识与 MCP 主题 | 无 | civ6_knowledge 全目录、knowledge.py、server 知识说明、知识相关测试；本机只读来源与构建 | in_progress |
| C2 本方和可见对手稳定身份 | 无 | 现有概览／外交的 Lua、模型、narrate 及对应公开测试；不改知识与文档 | in_progress |
| C3 三类信息决策指南 | 无 | AGENTS、docs/agent-strategy.md、docs/agent-playbook.md、根 README；不改知识与源码 | in_progress |
| C4 集成审查与离线验收 | C1、C2、C3 | 父任务协调独立 merger、全量离线、双轴审查、验收／索引／tracker 与工作树清理 | ready-for-agent |

代码使用此前确认的公开边界按 tdd 实施；单项独立 worktree，交付前同步集成分支。只读本机来源文件不构成游戏连接；所有任务不连接 FireTuner、不调用游戏工具、不改存档、不 push、不合并 main。
