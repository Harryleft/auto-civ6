# 贡献弹窗工作项

[规格](2026-10-19-contribution-popup-progress.md)，completed；集成分支codex/native-pillage。

| 项目 | 依赖 | 完成依据 | 状态 |
| --- | --- | --- | --- |
| P1 | 无 | 独立实施者在独立worktree用TDD修复MCP调度及直接相关测试 | completed：6cb488b |
| P2 | P1 | 独立merger合入、相关模块验证、编译和差分检查 | completed：55f692b，157项通过 |
| P3 | P2 | Standards／Spec独立审查，单一实施者修复发现 | completed：双轴0材料性问题 |
| P4 | P3 | 父任务更新客户端，实机关闭信息弹窗及原请求续行，完成终局、验收和保全 | completed：T231原请求确认；T232新版原生关闭，见验收 |

父任务维护规格和验收；实施者仅拥有server调度及相关测试，不操作游戏、不push、不撤销他人改动。
