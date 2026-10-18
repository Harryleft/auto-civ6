# 世界知识 MCP 工作项

规格：[世界知识只读 MCP 查询](2026-10-18-knowledge-mcp.md)。标签 ready-for-agent；本地 Markdown tracker；集成分支 codex/architecture-deepening。

| 工作项 | 依赖 | 所有权 | 状态 |
| --- | --- | --- | --- |
| K1 离线查询适配与 MCP 工具 | 无 | 单一实施任务：薄查询适配、server 工具／许可、公共 MCP 与临时知识回归 | ready-for-agent |
| K2 决策调用时机与文档简化 | 无 | 独立文档任务：AGENTS／策略／入口／安装文档，知识支持文件指纹同步 | ready-for-agent |
| K3 集成、双轴审查与离线验收 | K1、K2 | 父任务：独立 merger、全量离线、真实 MCP 协议、审查修复、存档保护与工作树清理 | ready-for-agent |

独立 worktree；代码按 tdd，文档仅必要校验。所有任务不连接游戏、不操作存档、不 push；保留当前分支。
