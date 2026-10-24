# 文档导航

`docs/` 保存跨对局复用的指南、实现说明、工程规格和验证依据。单局的回合记录、局面分析、存档清单、实机验收及随局修复资料统一放在 [games/](../games/README.md)。Agent 从 [AGENTS.md](../AGENTS.md) 进入，按任务选择文档。

## 当前指南

### 游戏操作

| 任务 | 入口 |
| --- | --- |
| 安装依赖、配置 FireTuner 或 MCP 客户端 | [安装与连接](installation.md)；[AI 安装提示词](../README.md#安装与启动) |
| 查询、常规动作和推进回合 | [游戏操作](agent-playbook.md) |
| 议会、国家间交易、外交对话、强制选择和信息弹窗 | [特殊操作指南](agent-special-operations.md) |
| 每局必看的六项指标、各胜利路线的先行信号与响应准备 | [策略检查清单](agent-checklist.md) |
| 扩张、胜利路线和对手预警 | [获胜决策](agent-strategy.md) |
| 赛后比较与因果归因 | [复盘方法](replay-analysis.md) |
| 保存、换档、结果未知和恢复 | [保存与恢复](agent-recovery.md) |
| 规则、解锁条件和稳定机制 | [知识查询](../civ6_knowledge/README.md#mcp-查询) |

### 开发操作

| 任务 | 入口 |
| --- | --- |
| 代码、测试、提交和文档归档 | [开发与验证](development.md) |
| 环境诊断、知识验证、领域测试和日志查询 | [离线调试 CLI](cli.md) |
| 状态归属、Lua 和通信机制 | [当前架构](architecture.md) |

## 规格与验收

| 资料 | 用途 |
| --- | --- |
| [工程规格](specs/README.md) | 跨对局的行为契约、设计范围及工作项；按主题查阅 |
| [工程验收](acceptance/README.md) | 项目能力的离线验证、覆盖清单及审查依据 |
| [原生热座双 AI 实验](specs/2026-10-24-native-hotseat.md) | macOS 热座专用 Tuner 补丁、固定席位入口及首轮实机结果；独立实验入口 |
| [状态变更全量验收](acceptance/2026-10-19-mutation-lifecycle.md) | 82 个入口、43 个变更工具的所有者、确认条件和显式边界 |
| [对局资料](../games/README.md) | 按对局和测试轮次查找回合过程、实机结果及原始证据位置 |

验收中的提交、测试数量和游戏结果只对应当时的受测版本。当前行为以指南和实现为准；需要核对某次实机成果时，从对局索引进入，保留原报告的范围与限制。

## 专项参考

| 主题 | 入口 |
| --- | --- |
| 对局、操作确认和策略观察的统一术语 | [术语表](../GLOSSARY.md) |
| 受信原始基准与文件指纹 | [存档说明](../fixtures/saves/README.md) |
| 存档结构分析 | [存档格式研究](save-file-format.md) |
| Windows Steam、编码和存档目录 | [Windows 运行说明](windows.md) |
| macOS Aspyr 启动器排查的历史方案 | [启动器调研](research/bypassing_aspyr_launcher.md) |
| 旧架构与设计探索的来源 | [设计归档](archive/README.md)；仅作历史参考 |
