# Civ 6 + DSH 启动与验收

本文是当前项目的运行手册。`AGENTS.md` 只保留硬规则和本文入口；DSH 集成的详细配置仍以 [`integrations/deepseek-harness/README.md`](../integrations/deepseek-harness/README.md) 为准。

## 启动链路

唯一支持的链路是：

```text
文明 VI → FireTuner 127.0.0.1:4318 → civ6-belief-engine 的 MCP 适配层 → DSH MCP 客户端
```

默认情况下 DSH 不负责启动文明 VI，必须先进入一局游戏；不要把 DSH Web 页面、Python 进程或 Civ 6 进程单独当成集成成功。若明确设置 `CIV_MCP_DSH_AUTO_RESUME=1`，DSH 才会在 MCP 启动时检查是否已进入对局，并通过 Civ VI 的 FrontEnd API 加载恢复存档和确认“继续游戏”，不依赖 GUI 点击或视觉识别。

## 一键启动脚本

日常启动可直接使用 `scripts/civ6_launch`，它会按顺序完成：预检（node/uv/DSH 构建、
`EnableTuner 1`、端口占用、单客户端限制）→ 启动游戏并等待 FireTuner → DSH overlay
检查 → 启动 DSH：

```bash
./scripts/civ6_launch          # 启动游戏（如未运行）并前台运行 DSH Web
./scripts/civ6_launch headless "查看当前回合，不要结束回合"   # 一次性 headless 会话
./scripts/civ6_launch agent --turns 1       # 受控回合循环（等价 scripts/civ6_agent）
./scripts/civ6_launch check    # 只做环境/overlay 检查，不启动任何进程
./scripts/civ6_launch status   # 只读查看链路各部分状态
./scripts/civ6_launch down     # 停止 DSH 及其 MCP 子进程（默认保留游戏；--game 一并退出）
```

该脚本是显式的游戏启动 opt-in：默认开启受控恢复（`CIV_MCP_DSH_AUTO_RESUME=1`），
游戏已在运行且 FireTuner 可访问时会跳过启动；`--no-game --no-resume` 可完全禁止
任何一方拉起游戏。`--timeout N` 调整 FireTuner 等待秒数。与 `deepseek_harness`
wrapper 相同，4318 已有已连接客户端或 8000 被占用时直接失败，不自动杀进程。

## 标准启动步骤

1. 确认 macOS 配置文件中的 FireTuner 已开启：

   ```text
   ~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/AppOptions.txt
   ```

   文件中必须有 `EnableTuner 1`。开启后成就统计会关闭。

2. 启动 Steam 版文明 VI：

   ```bash
   open 'steam://run/289070'
   ```

   如果出现 Aspyr 启动器，点击“开始”，然后进入单人游戏或载入存档，直到看到实际游戏局面。

3. 从 `civ6-mcp` 根目录检查 FireTuner 监听：

   ```bash
   cd /Users/zhuanzmima0000/Documents/ChatGPT/civ6/civ6-mcp
   lsof -nP -iTCP:4318 -sTCP:LISTEN
   ```

   必须看到 `127.0.0.1:4318`。只看到 Steam、Aspyr 或 `Civ6` 进程不够。

4. 确认 FireTuner 没有第二个客户端。停止 Pi、Codex、独立 `civ-mcp` 和连接测试客户端；FireTuner 实际上是单客户端连接。

5. 首次安装或配置变更后检查 DSH overlay：

   ```bash
   ./scripts/deepseek_harness check
   ```

   `check` 只验证 Python 包、DSH 构建和 overlay 组合，不启动游戏、不连接 FireTuner、不调用模型，也不执行回合。

6. 启动 DSH Web：

   ```bash
   ./scripts/deepseek_harness web
   ```

   打开 <http://127.0.0.1:3080>。不要另外执行 `uv run civ-mcp`；wrapper 会在 DSH 中挂载唯一的 Civ MCP 服务。

   如果希望由 DSH 自动启动游戏并进入最近恢复点，使用显式 opt-in：

   ```bash
   CIV_MCP_DSH_AUTO_RESUME=1 ./scripts/deepseek_harness web
   ```

   它优先加载 `0_MCP_*.Civ6Save`，没有可用文件时才加载 `AutoSave_*.Civ6Save`；若已经发现 `GameCore_Tuner` 和 `InGame`，则不会点击菜单或重载当前对局。该路径不使用 `CIV_MCP_SAVE_FILE` eval auto-boot，也不会清理 MCP 存档。

7. DSH 会话的第一条游戏请求必须是 `mcp__civ6__get_game_overview`，先读取局面和机器可读的 `RUNTIME POLICY`，确认成功后再执行查询或动作。

## 可选的连接测试

在没有其他 FireTuner 客户端时，可以运行：

```bash
uv run python tests/manual/test_connection.py
```

成功的最低证据是 TCP 连接、FireTuner handshake 和 Lua state 列表。错误 Lua state 中的 `Game.GetCurrentGameTurn()` 返回 `nil`，可能是状态不匹配，不等于握手失败。

## 完整验收门槛

只有全部条件满足，才可认为 DSH 能操作当前游戏：

1. 文明 VI 已进入一局，`127.0.0.1:4318` 正在监听。
2. DSH 启动并发现 `mcp__civ6__*` 工具。
3. `mcp__civ6__get_game_overview` 成功返回。
4. `RUNTIME POLICY` 中的 `belief_mode` 与实际配置一致；`enforce` 模式还应包含当前治理快照。
5. 读取成功后才允许执行动作或 `end_turn`。

如果 DSH wrapper 报 TCP 8000 被占用或 4318 已有 established client，先停止对应的旧主机/客户端；wrapper 不会自动杀进程。

## 相关文档

- [DSH 集成配置](../integrations/deepseek-harness/README.md)
- [系统架构](architecture-diagrams.md)
- [信念引擎](belief-engine.md)
- [治理系统](governance-system.md)
- 返回 [AGENTS.md](../AGENTS.md)
