# AGENTS

本项目让 AI 模型通过本机 `civ6` CLI 自动玩 Civilization VI。MCP 是 CLI 内部通信实现。

- 游戏引擎执行规则；
- 模型负责策略；
- 游戏运行层（Runtime）负责真实查询、单次提交、结果确认和显式生命周期控制。

## 必读约束

- 先读取真实游戏状态，再使用回合、坐标及实体 ID；后续决策以动作回读为依据，区分不可见、未知和不存在。
- 结果未知时保留未决请求，先只读核对；不盲目重发，不透明重启。普通身份查询和缓存刷新不能释放未决门禁。
- 新实机测试只从当前任务指定的受信原始基准开始；续测、恢复仅用当前测试轮次的已确认检查点。保留原始基准、现有存档和既有验收结果，过程与结果另存。
- 保持本机模型操作游戏的范围；不重新引入网页、云同步、后台观测／轮询服务、批量评测、强制反思或日记协议。
- 沿用当前任务分支并保留已有修改，只提交本次内容；用户另有分支要求时按其执行。未经明确授权不 push、不改写提交历史。
- 离线检查与实机操作分开。只有任务已授权时才操作真实游戏；交付时区分离线通过、实机通过和未验证。

## 调用入口

当前支持的 AI 操作统一以 [CLI](docs/cli.md) 为唯一入口，包含游戏、知识、诊断、验证和日志。连续游戏操作使用一个持续的 `uv run --locked civ6 debug` 会话，按 `help` 发现命名命令。`call` 仅用于同会话的协议兼容诊断；普通对局使用命名命令。变更先 `--dry-run`，再按当前授权明确执行，保留原 pending 和证据。

客户端不注册 `civ6` MCP；CLI 自行启动内部服务。出现 CLI 错误时按原会话证据修复，不改走直接 MCP、内部 Python 或 Lua。任意 Lua 仅在用户明确授权的 `debug --developer` 诊断会话可用。

既有双人热座实验通过 `debug --hotseat-seat N --hotseat-run DIR` 复用原控制器，使用 `hotseat begin/end`；普通 `turn end` 和 Single 存档入口不能替代席位交接。新地图、更多席位和补丁操作仍按各自指南验证。

唯一历史例外是 `scripts/hotseat_endurance.py`：它仅为复现 2026-10-24 实验而保留直接 MCP 注入，必须显式 `--acknowledge-legacy-mcp`，不得用于新对局或替代当前 CLI 入口。

## 按任务读取

先按任务类型选择游戏操作或开发操作；实机验收同时读取开发验证与对应游戏流程。只展开当前任务命中的链接。

| 任务触发条件 | 读取位置 |
| --- | --- |
| 使用或维护 CLI、执行日常工具调用 | [CLI 命令手册](docs/cli.md) |
| 理解或修改项目术语、区分易混概念 | [术语表](GLOSSARY.md) |

### 游戏操作

| 任务触发条件 | 读取位置 |
| --- | --- |
| 启动、配置或连接游戏 | [安装与连接](docs/installation.md) [游戏操作：开始运行](docs/agent-playbook.md#开始运行) |
| 开局、续局或推进以获胜为目标的对局 | [每局策略检查清单](docs/agent-checklist.md) |
| 查询、执行常规动作或推进回合 | [游戏操作](docs/agent-playbook.md) |
| 开局识别文明／领袖、理解已相遇对手，或制定与调整策略 | [获胜决策](docs/agent-strategy.md) |
| 文明／领袖能力、专属内容或通用机制不清楚 | [规则知识查询](civ6_knowledge/README.md#mcp-查询) |
| 保存、换档、结果未知、断线或挂起 | [保存与恢复](docs/agent-recovery.md) |
| 记录或查阅单局过程、回合验收、复盘和存档证据 | [对局资料](games/README.md) |

### 特殊操作

出现以下交互状态或推进阻塞时，读取对应章节。

| 任务触发条件 | 读取位置 |
| --- | --- |
| 议会待办、会期或投票阶段变化 | [世界议会](docs/agent-special-operations.md#世界议会) |
| 主动国家间交易、来约或反报价 | [国家间交易](docs/agent-special-operations.md#国家间交易) |
| 外交要求、谴责、承诺或其他会话 | [外交对话](docs/agent-special-operations.md#外交对话) |
| 时代着力点、间谍逃离或城市决定待办 | [强制选择](docs/agent-special-operations.md#强制选择) |
| 历史时刻、科技完成等信息界面暂停 | [信息弹窗](docs/agent-special-operations.md#信息弹窗) |

### 开发操作

| 任务触发条件 | 读取位置 |
| --- | --- |
| 修改代码或文档、测试、提交、审查 | [开发与验证](docs/development.md) |
| 修改状态归属、Lua 解释或通信机制 | [当前架构](docs/architecture.md) |
| 核对基准身份、工程规格或离线验收 | [存档说明](fixtures/saves/README.md) [文档索引](docs/README.md#规格与验收) |

## 基准测试存档

| 序号 | 基准测试地图名称 | 在项目中的位置 | 简要介绍（领袖、难度、对手） |
| --- | --- | --- | --- |
| 1 | `benchmark_turn_1` | [fixtures/saves/benchmark_turn_1.Civ6Save](fixtures/saves/benchmark_turn_1.Civ6Save) | 盘古大陆、极小；苏美尔／吉尔伽美什；不朽；对手：瑞典、巴西、加拿大 |
| 2 | `benchmark_gold_tun_1` | [fixtures/saves/benchmark_gold_tun_1.Civ6Save](fixtures/saves/benchmark_gold_tun_1.Civ6Save) | 四叶草、极小；苏美尔／吉尔伽美什；神级；对手：瑞典、巴西、加拿大 |
