# DeepSeek Harness integration

This integration mounts `civ6-mcp` into DeepSeek Harness through DSH's built-in MCP client. It deliberately keeps the two runtimes separate: DeepSeek Harness owns the model session and agent loop, while `civ6-mcp` remains the sole owner of game state, rules, action authorization, FireTuner, saves, and game telemetry.

## Architecture

| Concern | Owner | Integration rule |
|---|---|---|
| Model provider, streaming, agent loop, compaction | DeepSeek Harness | Use the shipped DeepSeek adapter and durable DSH session log. |
| Tool discovery and execution history | DeepSeek Harness | `@deepseek-ai/dsh-mcp-client` exposes tools as `mcp__civ6__<tool>`. |
| Typed game state | `GameState` in `civ6-mcp` | Do not mirror it into a second DSH state store. Query it through MCP. |
| Strategic memory and governance | Belief Engine and governance modules in `civ6-mcp` | Keep the existing same-turn snapshot, proposal, council, and ActionIntent gates authoritative. |
| End-turn safety | `end_turn.py` in `civ6-mcp` | DSH supplies the call; Civ 6 validates blockers and advances the turn. |
| Game connection and lifecycle | `GameConnection` and game lifecycle modules in `civ6-mcp` | Keep exactly one FireTuner client. DSH must not launch a second MCP host. |
| Game telemetry and saves | `TelemetryEmitter`, diary, autosave, and watchdog in `civ6-mcp` | DSH records the model/tool transcript; Civ 6 records domain facts and recovery points. |

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

Open `http://127.0.0.1:3080`. DSH loads this repository's `AGENTS.md` because the launcher keeps `civ6-mcp` as the working directory. Begin each game turn with `mcp__civ6__get_governance_brief` and follow the existing turn loop.

For a one-shot headless task:

```bash
./scripts/deepseek_harness headless "Inspect the current turn; do not end it."
```

## Safety decisions

- The raw `run_lua` tool is disabled for this integration. Domain tools remain the supported game interface.
- `CIV_MCP_SAVE_FILE` is cleared so starting DSH cannot trigger eval auto-boot or load a save.
- MCP tool calls allow 15 minutes because Deity AI turns can exceed DSH's one-minute default.
- DSH child reconnection is disabled. If `civ-mcp` exits, stop and restart the DSH host after confirming no stale FireTuner client remains.
- The launcher refuses to start when TCP 8000 already has a listener or TCP 4318 already has an established client. It never kills those processes automatically.
- A DSH Web session log and the Civ 6 Belief Engine are complementary, not interchangeable. The session log reconstructs model-visible history; the Belief Engine remains the authoritative strategic and governance state.

## Verification boundary

`check` proves installation and composition. A complete live acceptance still requires this sequence:

1. Civilization VI is running and TCP 4318 is listening.
2. The DSH host starts and discovers `mcp__civ6__*` tools.
3. `mcp__civ6__get_governance_brief` succeeds against the current game.
4. A harmless read such as `mcp__civ6__get_game_overview` succeeds.
5. Only after those reads pass should the agent execute a governed action or call `end_turn`.

Do not treat a DSH Web page, a Python process, or a TCP listener alone as proof that the game integration is usable.
