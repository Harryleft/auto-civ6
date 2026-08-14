# civ6-belief-engine — 文明 VI MCP 智能体规则入口

这是通过 FireTuner 连接正在运行的《文明 VI》的 MCP 服务。智能体只能知道明确查询过的信息；所有游戏动作都必须经过当前游戏规则和 MCP 服务校验。

## 不可违反的边界

- 当前唯一运行链路是：`文明 VI → FireTuner 127.0.0.1:4318 → civ6-belief-engine 的 MCP 适配层 → DSH`。
- DSH 不直接启动文明 VI。必须先进入一局游戏，再启动 DSH。
- FireTuner 只允许一个客户端。不要并行运行 Pi、Codex、独立 `civ-mcp`、连接测试或第二个 DSH MCP 客户端。
- Civ 6 进程、DSH 页面、Python 进程或 4318 监听单独存在，都不等于集成可用。
- `CIV_MCP_BELIEF_MODE` 和 `get_game_overview` 返回的 `RUNTIME POLICY` 是信念/治理能力的唯一来源；不要从本文或旧会话猜测运行模式。
- `get_game_overview` 是每回合唯一的强制入口；读取成功前不要执行游戏动作或 `end_turn`。
- 如果返回 `BELIEF_GATE_REQUIRED`，按返回的精确 action intent 完成路由后只重试一次；`bypassed` 模式不要调用路由工具。
- 原始工具结果归 DSH transcript/telemetry；信念引擎只保存规范化事实、指纹和决策/动作关联。

## 最短启动顺序

1. 在 macOS `AppOptions.txt` 中确认 `EnableTuner 1`。该设置会关闭成就统计。
2. 启动游戏：

   ```bash
   open 'steam://run/289070'
   ```

   出现 Aspyr 启动器时点击“开始”，并进入单人游戏或载入存档。
3. 验证端口：

   ```bash
   cd /Users/zhuanzmima0000/Documents/ChatGPT/civ6/civ6-mcp
   lsof -nP -iTCP:4318 -sTCP:LISTEN
   ```

4. 首次安装或变更配置后运行 `./scripts/deepseek_harness check`。
5. 启动 DSH：

   ```bash
   ./scripts/deepseek_harness web
   ```

   打开 <http://127.0.0.1:3080>。不要另外运行 `uv run civ-mcp`。
6. DSH 第一条游戏请求调用 `mcp__civ6__get_game_overview`，确认 `RUNTIME POLICY` 和工具读取成功后，才进入回合流程。

完整启动与验收步骤见 [启动与验收](docs/agent-startup.md)。

## 每回合最小流程

1. `get_game_overview`：读取当前回合、规则集、运行策略和治理状态。
2. 立即调用 `get_barbarian_overview`；营地是刷兵源头，不能只看当前可见的蛮族单位。
3. 有营地或蛮族单位时，先分配军事单位清剿；攻击前调用 `get_combat_estimate`，`move/attack` 的治理门禁按返回的精确 intent 处理。
4. 再按下一步决策需要查询 `get_units`、目标区域的 `get_map_area`、`get_cities` 等，不复制原始结果到信念或日志。
5. 处理治理/信念门禁，执行动作并确认结果；在可行动兵力存在时，不得把未清理的近城营地留到扩张之后。
6. 重要新证据、动作结果或上下文恢复后才调用 `get_turn_brief`。
7. `skip_remaining_units` → `end_turn`；服务器阻塞项和蛮族告警高于旧状态和提示词。

回合顺序、Diary 字段和 10/20/30 回合检查见 [回合规则](docs/agent-turn-loop.md)。

## 策略与工具路由

按需读取，不要把所有文档全文复制进会话：

| 任务 | 文档 |
|---|---|
| 启动 DSH、验证 FireTuner、判断是否真的可用 | [docs/agent-startup.md](docs/agent-startup.md) |
| 回合顺序、坐标、信念/治理门禁、Diary、周期检查 | [docs/agent-turn-loop.md](docs/agent-turn-loop.md) |
| 神级生存、扩张、外交、战争和胜利路线 | [docs/agent-strategy.md](docs/agent-strategy.md) |
| 工具、单位动作、阻塞项、生产、区域、商路和世界议会 | [docs/agent-tools.md](docs/agent-tools.md) |
| 自动存档、卡回合和恢复 | [docs/agent-recovery.md](docs/agent-recovery.md) |
| 信念事件、运行模式、规范化事实和路由 | [docs/belief-engine.md](docs/belief-engine.md) |
| 提案、批评、议会、预算锁和 ActionIntent | [docs/governance-system.md](docs/governance-system.md) |
| MCP 到 Lua、FireTuner 单连接和游戏引擎架构 | [docs/architecture-diagrams.md](docs/architecture-diagrams.md) |
| 产品名、领域包边界和 MCP 兼容命名 | [docs/product-architecture.md](docs/product-architecture.md) |
| 图工程当前方案与实施路线 | [graph_plan/README.md](graph_plan/README.md) |
| DSH overlay 配置和安全决策 | [integrations/deepseek-harness/README.md](integrations/deepseek-harness/README.md) |

## 决策硬规则

- 移动建造者、开拓者或商人前，先检查目的地及周围地块；长距离移动前使用 `get_pathing_estimate`。
- 闲置建造者、商路、金币和信仰都是可见的机会成本；除非有明确目标和回合，不要无计划囤积。
- 每座城市关注城市增长、生产、忠诚度、城墙和驻军；城市数量是长期复利的主要来源。
- 对手达到 2 倍以上军力、在边境集结攻城单位或接近任何胜利条件时，提高检查频率。
- `get_barbarian_overview` 返回的营地优先于泛化的“附近敌人”：近城营地先清剿，击杀波次不等于消除刷兵源。
- 宣战回合不能攻击新敌人；战斗引擎下一回合才同步。
- `end_turn` 可能在神级 AI 回合中耗时 5–10 分钟；失败时先读取错误和当前状态，不要盲目循环重试。
- MCP 自动存档是主要恢复点；错误加载存档后以 `end_turn` 的 CRITICAL 警告为准。

## 开发与验证命令

```bash
uv run pytest tests/ -q --ignore=tests/test_scorer.py     # 全量离线测试（test_scorer.py 是已知收集失败：根目录 evals 包未安装）
uv run pytest tests/test_belief_engine.py -q -k "orphan"  # 按关键字跑单个测试
./scripts/deepseek_harness check                          # 安装/配置变更后的环境检查
```

- 无独立 lint/format 配置；代码风格、注释密度跟随各文件现状。
- 不要单独运行 `uv run civ-mcp`：DSH 负责拉起 MCP 进程，FireTuner 只允许一个客户端。
- 新增游戏动作必须走 `execute_mutation` 通道（见下节），并配离线回归测试。

## 架构大图（跨文件）

- **双包布局**：`src/civ_mcp` 是 MCP 适配层（连接、Lua builder/parser、server.py 的 90+ 工具），`src/civ6_belief_engine` 是产品域包（belief engine + governance）。`civ_mcp.belief_engine` 和 `civ_mcp.governance` 只是兼容转发 shim。已知债务：域包 `governance/{snapshot,models}.py` 仍 import `civ_mcp.lua.models`，依赖方向待倒置（graph_plan 阶段一）。
- **调用链**：FireTuner TCP 4318 → `GameConnection` → `GameState` → `server.py` 的 `_logged` 管道（授权预检 → 执行 → 信念记录 → 结果过滤）→ DSH。所有 MCP 工具必须经 `_logged`，不要绕开。
- **命名陷阱**：`execute_read`/`execute_write` 指的是 Lua 上下文（GameCore/InGame），**不是**读写语义。真正的读写区分在 `execute_mutation`：变异命令恰好发送一次、死套接字不重发（抛 `MutationOutcomeUnknownError`，重试前必须先查询验证游戏状态）、未收到 sentinel 超时抛 `CommandTimeoutError`。
- **BeliefEngine 是事件溯源**：append-only JSONL（`~/.civ6-mcp/beliefs/`），加载时 reduce 重放；实体用墓碑（deleted/archived）不物理删除；游戏重载（autosave 回滚/手动读档）产生 epoch 标记并作废旧授权；`governance_turn_gate` 对未完成授权 fail-closed。新代码不得绕过事件流直接改内存态。
- **结果过滤只作用于模型面副本**：`result_filter` 在返回给 DSH 前压缩超大结果，遥测保留原始全文；阈值由 `CIV_MCP_RESULT_*` 环境变量控制。
- **图工程路线图**（阶段零 P0 已完成，后续阶段见完成标准）在 [graph_plan/README.md](graph_plan/README.md)，动架构前先读它。

## 代码和文档边界

- 当前游戏状态、规则和动作授权由产品的 `civ_mcp` MCP 适配层负责；不要在 DSH 中建立第二套 GameState 或生命周期。
- `run_lua` 在 DSH 集成中不是支持的游戏接口；优先使用领域工具。
- 修改游戏机制时同步阅读 [架构](docs/architecture-diagrams.md)、[信念引擎](docs/belief-engine.md) 和 [治理系统](docs/governance-system.md)。
- 修改启动、连接或恢复行为时同步更新 `docs/agent-startup.md`、`docs/agent-recovery.md` 与 DSH 集成说明。
- 运行验证时区分离线测试、FireTuner handshake、MCP 工具读取和真实回合动作；不能用前者冒充后者。

项目文档总目录见 [docs/README.md](docs/README.md)，安装和基础用法见 [README.md](README.md)。
