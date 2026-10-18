# 失去路径队列取消工作项

规格：[取消失去路径的移动队列](2026-10-18-cancel-unpathable-queue.md)。ready-for-agent；集成分支 `codex/cancel-unpathable-queue`，基点 `3a26445`。

| 项目 | 依赖 | 所有权及完成条件 | 状态 |
| --- | --- | --- | --- |
| Q1 | 无 | 单一实施者修改准备事实、取消入口和相关测试，TDD 复现并修复 | ready |
| Q2 | Q1 | 独立 merger 合入，运行相关测试和差分检查 | blocked |
| Q3 | Q2 | Standards／Spec 两轴独立审查，单一实施者修复发现 | blocked |
| Q4 | Q3 | 父任务实机续推至 T121，保存恢复、保护核对和本地提交 | blocked |

父任务拥有规格、tracker、验收文档。既有 GameState／FakeConn、Lua 5.1、MCP 为测试边界；不连接游戏、不全量测试。保留他人修改。
