# 来约 P2 修复工作项

[规格](2026-10-19-incoming-p2-boundaries.md)，Status: ready-for-agent。

| 工作项 | 依赖 | 所有权与验收 | 状态 |
| --- | --- | --- | --- |
| P1 | 无 | 实施者拥有外交来约实现、Lua 与相关测试；逐项 TDD 修复 ZERO 确认和独立世界失效锁存，不改父任务 tracker、不操作游戏 | ready-for-agent |
| P2 | P1 | 独立 merger 集成并核对相关离线验证与变更范围 | blocked |
| P3 | P2 | Standards／Spec 独立审查，修复发现后复查 | blocked |
| P4 | P3 | 父任务记录验收与文件保全、更新索引，合入 main；不 push | blocked |

两项修复共享同一来约确认路径，由单一实施者依次完成，避免并行修改同一所有者。父任务拥有规格、tracker、索引和验收文档。
