# DeepSeek Harness integration

This integration mounts the `civ6-belief-engine` product into DeepSeek Harness through DSH's built-in MCP client. Its unchanged `civ_mcp` adapter remains the sole owner of game state, rules, action authorization, FireTuner, saves, and game telemetry; the repository and MCP compatibility names still use `civ6-mcp`/`civ-mcp`.

## Architecture

| Concern | Owner | Integration rule |
|---|---|---|
| Model provider, streaming, agent loop, compaction | DeepSeek Harness | Use the shipped DeepSeek adapter and durable DSH session log. |
| Tool discovery and execution history | DeepSeek Harness | `@deepseek-ai/dsh-mcp-client` exposes tools as `mcp__civ6__<tool>`. |
| Typed game state | `GameState` in `civ6-mcp` | Do not mirror it into a second DSH state store. Query it through MCP. |
| Strategic memory and governance | `civ6_belief_engine` domain package | `get_game_overview` reports the active policy; when enabled, its snapshot, proposal, council, and ActionIntent gates are authoritative. |
| End-turn safety | `end_turn.py` in `civ6-mcp` | DSH supplies the call; Civ 6 validates blockers and advances the turn. |
| Game connection and lifecycle | `GameConnection` and game lifecycle modules in `civ6-mcp` | Keep exactly one FireTuner client. DSH must not launch a second MCP host. |
| Transcript, telemetry, and saves | DSH session log plus `TelemetryEmitter`, diary, autosave, and watchdog in `civ6-mcp` | DSH/telemetry own raw tool results; the Belief Engine stores normalized facts, fingerprints, and entity links instead of another raw copy. |

The resulting call path is:

```text
DeepSeek model
  -> DSH agent loop and durable session log
  -> DSH MCP client
  -> civ6-mcp tools and governance gates
  -> GameState / FireTuner
  -> Civilization VI
```

There is no custom Cordis game runtime in this version. The official MCP bridge already provides the required extension seam, and duplicating the Python state and lifecycle logic would create two authorities for one live game.

## Installed layout

The expected local layout is:

```text
civ6/
  deepseek-harness/   # official source checkout, installed and built separately
  civ6-mcp/           # this repository
```

The launcher also accepts an explicit checkout:

```bash
DEEPSEEK_HARNESS_DIR=/absolute/path/to/deepseek-harness \
  ./scripts/deepseek_harness check
```

DSH runtime state, sessions, and local credentials default to the ignored `.dsh/` directory in this repository. Set `CIV6_DSH_HOME` to use another directory.

## Verify the installation

From the `civ6-mcp` repository root:

```bash
./scripts/deepseek_harness check
```

This imports the Python package and asks DSH to compose the Web profile with the Civ 6 overlay. It does not start Civilization VI, connect to FireTuner, call a model, or execute a game tool.

## Run

1. Export `DEEPSEEK_API_KEY`, or configure the DeepSeek provider in the DSH Web UI.
2. Start Civilization VI with FireTuner enabled and load a game.
3. Stop every other `civ-mcp`, Pi, Codex, or test client connected to TCP 4318.
4. Start the integrated Web UI:

```bash
./scripts/deepseek_harness web
```

By default this assumes Civilization VI is already in a game. To explicitly
let DSH start the game and load a recovery point through the existing GUI menu
flow, opt in for that process:

```bash
CIV_MCP_DSH_AUTO_RESUME=1 ./scripts/deepseek_harness web
```

For a full one-command bring-up that also launches the game and waits for
FireTuner, use the launcher (game startup is its explicit opt-in; it enables
controlled recovery by default and can be disabled with `--no-resume`):

```bash
./scripts/civ6_launch                # game + DSH Web (http://127.0.0.1:3080)
./scripts/civ6_launch check|status|down   # readiness, read-only status, teardown
```

The recovery selector prefers the newest non-empty `0_MCP_*.Civ6Save` in the
regular Single saves directory, then falls back to `AutoSave_*.Civ6Save` in
the autosave directory. If FireTuner already reports both `GameCore_Tuner`
and `InGame`, no menu click or save reload is attempted. The opt-in path is
separate from the eval-only `CIV_MCP_SAVE_FILE` auto-boot and never removes MCP
saves.

Open `http://127.0.0.1:3080`. DSH loads this repository's `AGENTS.md` because the launcher keeps `civ6-mcp` as the working directory. Begin each game turn once with `mcp__civ6__get_game_overview`, then follow its `RUNTIME POLICY` instead of a duplicated mode-specific prompt.

For a one-shot headless task:

```bash
./scripts/deepseek_harness headless "Inspect the current turn; do not end it."
```

### 一站式文明智能体入口

不需要 DSH Web UI 时，使用下面唯一的入口：

```bash
./scripts/civ6_agent --turns 1
```

它会开启受控恢复（仅在游戏尚未进入对局时），启动无网页的 DSH headless
会话，并让同一个 DSH 会话连续完成指定数量的完整回合。默认只执行 1 回合；先验证
`--turns 1`，再逐步提高到 3 或更多。该入口默认叠加精简的
`civ6-agent.cordis.yml`：`mcp__civ6__*` 是唯一的游戏控制路径，Coding 工具不会进入
普通回合循环。用户明确需要局势可视化、日志分析、策略推演或报告时，再显式启用模块：

```bash
./scripts/civ6_agent --turns 1 --with-coding
```

`--with-coding` 叠加 `civ6-agent-coding.cordis.yml`，只恢复本地 Coding 能力，仍不能
读取或伪造游戏状态、调用 FireTuner，或绕过治理门禁。普通回合中，MCP/治理错误是明确
阻塞项，不能回退为读取项目源码来猜测解决。网页、技能、命令与目标类工具仍被禁用。

启动前的无副作用检查：

```bash
./scripts/deepseek_harness check
./scripts/civ6_agent --turns 1 --dry-run
```

仍然只能保留一个 FireTuner 客户端；已有 DSH、Codex、Pi 或测试客户端连接 TCP 4318
时，入口会明确失败而不会自动终止其他进程。达到指定回合数、遇到治理/人类决策门禁、
游戏结束或连接异常时，智能体会停止并给出中文结果，而不是无限运行。

### 游玩配置（play profile）

`--play-profile legacy|lean` 选择运行哪条游玩路径，默认 `legacy`：

```bash
./scripts/civ6_agent --play-profile lean --turns 1 --dry-run   # 预览实际生效配置
./scripts/civ6_agent --play-profile lean --turns 1              # 操作游戏，需现场授权
./scripts/civ6_agent --play-profile legacy --turns 1           # 回到原路径
```

`lean` 使 MCP 子进程在 `CIV_MCP_BELIEF_MODE=off` 下运行、移走 29 个信念/治理控制面
工具（112 → 83），并叠加 `civ6-lean.cordis.yml` 的精简角色。`--play-profile lean`
与显式 `CIV_MCP_BELIEF_MODE=observe|enforce` 冲突时以退出码 2 报错，不静默覆盖。
详细契约、工具名单、反思字段语义与回退方式见
[精简游玩配置](../../docs/lean-play-profile.md)。

工具面的收窄**必须在 MCP 子进程内完成**：`@deepseek-ai/dsh-mcp-client` 的配置字段只有
`transport`/`serverName`/`command`/`args`/`env`/`cwd`/`url`/`headers`/`toolCallTimeoutMs`/
`failOnStartupError`/`reconnect`，没有任何 allowlist 或 denylist，`syncTools()` 会注册服务端
广告的每一个工具。因此 `civ6.cordis.yml` 只负责把 `CIV_MCP_PLAY_PROFILE` 与
`CIV_MCP_BELIEF_MODE` 传进子进程，由子进程在自己回答 `tools/list` 之前完成删减。
注意 DSH 的 `config` 补丁是整体替换而非深合并，且 Schemastery 会静默接受未知键——
写一个不存在的 `toolFilter:` 不会报错，也不会有任何效果。

## Safety decisions

- The raw `run_lua` tool is disabled for this integration. Domain tools remain the supported game interface.
- `CIV_MCP_SAVE_FILE` is cleared so starting DSH cannot trigger eval auto-boot or load a save. Startup recovery is separately controlled by `CIV_MCP_DSH_AUTO_RESUME` and defaults to off.
- MCP tool calls allow 20 minutes. That is an envelope for one slow Deity AI
  turn, not a typical duration and not a vote: `civ_mcp.end_turn` derives
  poll 830s + query reserve 300s = 1130s, and the timeout applies per call so it
  must exceed the *longest single call*. `tests/test_end_turn_budget.py` fails if
  this value ever drops to or below that ceiling. The flow also enforces it from
  the inside and returns an `UNKNOWN:END_TURN_BUDGET_EXHAUSTED` receipt instead
  of letting the host kill the call mid-advance.
- **Recovery is a separate step, never part of `end_turn`.** A wedged AI turn
  needs a relaunch and an autosave reload, and running up to three of those
  inside `end_turn` is what used to force a ~50-minute deadline — while also
  hiding whether a turn had merely been slow or had been wedged and restarted.
  On a hang, `end_turn` now returns `HANG:<turn>:<save>` plus an explicit next
  step (`restart_and_load`, then verify with `get_game_overview`), and refuses to
  invite a resend. A "hang" is never inferred on a congress turn.
- A World Congress `end_turn` is **not** inherently a long call. The congress
  opens inside `ACTION_ENDTURN` and parks on its screen; waiting passively for it
  was what made those turns take 10-20 minutes, and — worse — an undriven
  congress used to be misread as a wedged AI turn, which killed and reloaded a
  healthy game up to three times over a missing vote. The driver votes the live
  resolutions and submits the session from Lua (what a human does), running from
  t+5s and covering the whole session-opening window, so those turns normally
  resolve in seconds.
- DSH child reconnection is disabled. If `civ-mcp` exits, stop and restart the DSH host after confirming no stale FireTuner client remains.
- The launcher refuses to start when TCP 8000 already has a listener or TCP 4318 already has an established client. It never kills those processes automatically.
- `scripts/civ6_agent` is intentionally bounded by `--turns`; a bounded run makes a
  crash, policy gate, or model mistake observable and recoverable before another
  game turn is committed.
- A DSH Web session log and the Civ 6 Belief Engine are complementary, not interchangeable. The session log reconstructs model-visible history; the Belief Engine keeps only the normalized strategic/governance projection and references back to tool results.

## Verification boundary

`check` proves installation and composition. A complete live acceptance still requires this sequence:

1. Civilization VI is running and TCP 4318 is listening.
2. The DSH host starts and discovers `mcp__civ6__*` tools.
3. `mcp__civ6__get_game_overview` succeeds and reports the expected
   `belief_mode` in `RUNTIME POLICY`.
4. In `enforce` mode, that overview also contains a current governance
   snapshot; lightweight modes explicitly report governance as disabled.
5. Only after this read passes should the agent execute an action or call
   `end_turn`.

Do not treat a DSH Web page, a Python process, or a TCP listener alone as proof that the game integration is usable.
