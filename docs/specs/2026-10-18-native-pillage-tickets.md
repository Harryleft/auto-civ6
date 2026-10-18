# 原生掠夺工作项

[规格](2026-10-18-native-pillage.md)，ready-for-agent。集成分支 `codex/native-pillage`，基点 `0553bc1`。

| 项目 | 依赖 | 所有权与完成依据 | 状态 |
| --- | --- | --- | --- |
| P1 | 无 | 独立实施者负责单位掠夺 Lua、GameState、MCP、相关测试及操作指南，TDD 验证 | ready |
| P2 | P1 | 独立 merger 合入并运行相关模块测试、编译与差分检查，记录指纹 | blocked |
| P3 | P2 | Standards／Spec 两轴独立审查；单一实施者修复发现 | blocked |
| P4 | P3 | 父任务重建单一 MCP 客户端，核实当前局面并实际掠夺，继续至原生胜负，保存阶段档及证据 | blocked |

父任务拥有规格、tracker、验收文档；实施者不操作游戏、不 push、不修改其他工作项，不撤销他人改动。
