# Changelog

## Unreleased

The focus shifted from running games to packaging the results. The dataset publisher pipeline exports all telemetry to HuggingFace with Croissant 1.1 metadata for the NeurIPS Evaluations & Datasets track submission. Several reliability features landed in parallel from ongoing eval runs across the fleet.

- **架构评审修复（21 个提交）**：一次只读评审驱动，每项独立验证并做反向验证（撤掉修复后回归测试必须转红）。分四类：

  **P0（阻断级）**
  - `assembly.lifespan` 的 `CIV_MCP_SAVE_FILE` 分支既不启动后台服务也不置位 `auto_resume_ready`，而 `pipeline._logged` 首行就 await 它——eval 启动路径上**所有**经 `_logged` 的工具永久挂死（`evals/civbench.py` 恰好走这条分支）。由 29630ba 引入，此后无分支测试覆盖。
  - `hypothesis` 被 3 个测试模块导入却未声明在任何依赖组，也未进 `uv.lock`。pytest 遇收集错误会中断整个运行，故全新克隆与 CI 上「一个测试都不跑」，而作者本机因手工装过而全绿。
  - `[tool.pytest.ini_options] pythonpath = ["."]`：`pytest` 控制台脚本不加 CWD 到 `sys.path`，与 `python -m pytest` 行为不一致，`tests/test_scorer.py` 的 4 个回归测试被无效的 `--ignore` 掩盖。
  - 测试写用户真实账本：`test_gate_fixes.py` 构造 `BeliefEngine` 时省略 `directory=`，把夹具记录追加进 `~/.civ6-mcp/beliefs/`。新增 autouse 夹具重定向默认目录，并抽出 `default_beliefs_directory()` 作为可注入接缝。
  - 图重放 Θ(T²) → **实测 43MB 日志加载 48.4s 降到 2.37s（20 倍）**。`replay_graph_events` 每个 delta 后重算全视图 `state_hash`（O(D×V)）；新增 `verify` 参数，默认只校验最新 checkpoint，`"all"` 保留逐 delta 模式用于定位分歧点。同时把 `bind_game` 移出事件循环（thread + double-checked lock）。

  **授权与账本完整性**
  - **关闭议会批准后可改写意图的越权路径**。此前路由拿 candidate_hash 对比提案的**当前** `action_intents`，从不校验版本；改写提案即可把对 A 的批准重定向到 B（而预算锁与优先级都是按 A 算的）。现在议会决议写入 `approved_intents` 内容指纹，路由要求提案内容与之相等；`engine.update` 另拒绝改写 decision 的 `action_intent` / `council_decision_id` / `args_hash`。
  - `authorize_action` 按 decision 的**创建 epoch** 兜底：`record_game_reload` 会作废被放弃分支上的授权，但进程可能在写入标记后、逐条作废前崩溃。用创建期而非最新事件，是因为加载期恢复会以当前 epoch 重新盖戳。
  - 所有读档路径补记 epoch：`restart_and_load` 工具与 end_turn 挂起恢复此前都不记，账本仍描述游戏已不存在的未来。
  - 账本加单写者文件锁（`LOCK_EX|LOCK_NB`）：此前无锁，第二个写入者会交错序列号，且 `_load` 遇撕裂行时的重写可能永久丢弃另一写者的事件。

  **门禁与静态检查**
  - 门禁分类显式化：112 个工具中 84 个此前走「不在集合里就放行」的隐式默认，新增工具即自动获得未受治理的执行路径。现在未归类一律 fail-closed，`test_tool_gate_coverage.py` 守护完整性。
  - 接入 `ruff` 窄规则集（未定义名/重复定义/assert 与异常误用/语法错误）。首次即查出 `narrate.py` 一处**可达的 `NameError`**（`_describe_trade_item` 在全仓从未定义，而 `narrate_test_trade` 经 `test_trade` 工具可达）与 `game_state.py` 缺失的 `Any` 导入。
  - 重命令使用 `SLOW_MUTATION_TIMEOUT`(30s) 而非 5s 默认值：`skip_remaining_units` / `set_policies` / `submit_congress` / `queue_wc_votes` 会让游戏同步做实际工作，超时被包装成 `MutationOutcomeUnknownError` 属假阳性。
  - `bayes.posterior` 溢出时报错而非静默返回全 0（0.0 能通过概率校验并落盘，等于一次证据更新抹掉整个假设池）。
  - `_belief_tool` 补齐 `auto_resume_ready` 等待与兜底 `except`，与 `_logged` 对齐。

  **CI**
  - CI 此前**从未运行过**（fork 上 Actions 默认关闭，且 `origin` 落后 138 个提交）。启用后依次暴露并修复了三个只在干净环境失败的问题：改用 `uv sync --locked` 安装（否则缺 hypothesis）、web job 从未跑过 vitest（20 个测试）、`bun run lint` 的 55 项既有积压与 `tsc` 因缺 fumadocs 生成的 `.source/` 必失败（改用 `postinstall: fumadocs-mdx`）。当前两个 job 全绿。
  - CI 补 `uv run ruff check src tests`、`bun run test`、依赖缓存。

- **双轨工具结果合并为单轨（信封 facts）**: 只读查询工具的信封移除了叙述文本轨 `narrated` —— 41 个信封 builder 与全部工具调用点不再生成/携带叙述文本；信封格式收敛为 `{v, tool, turn, source, coverage, facts}`。`normalize_tool_result` 对信封直接从字段级 `facts` 提取 observation facts 与 metrics（reliability 1.0），不再对叙述文本做正则解析；仅 `get_game_overview` 保留纯文本正则路径（其余 8 个工具的正则分支降级为纯文本兼容回退，供历史/降级场景）。配套：`parse_envelope` 不再要求 `narrated`（旧日志带 `narrated` 的信封仍可解析，向后兼容）；diplomacy 信封新增 `our_military` 字段（原叙述文本 "vs our N" 的唯一事实来源，rival 威胁派生规则依赖它）；修复 `victory_progress_envelope` 对 `enabled_victories` set 的 JSON 序列化崩溃（预存 bug）；删除 22 个无引用的叙述函数（约 900 行）；观察 statement 对信封结果回退为 "Observed result from {tool}"。

- **Server package split & governance adapter migration**: `server.py` was split move-only into the `server/` package (2026-08-15: `assembly.py` lifespan/entry/auto-resume, `pipeline.py` runtime pipeline, `tools/` domain-grouped tool registration). The legacy `tools/belief.py` was then physically deleted (2026-08-16): 26 MCP tools moved to `tools/belief_tools.py`, pure contract adapters to `tools/governance_adapters.py`, typed snapshot lifecycle to `server/governance_snapshot.py`; `end_turn.py` now only registers the MCP tool — diary merge, pending-recovery, and game-over orchestration live in `end_turn_flow.py`. `server/__init__.py` preserves the stable import surface; MCP tool names and signatures unchanged. The domain package now imports nothing from `civ_mcp` (residual DTO debt closed); all seven departments consume the narrow `GraphSnapshotView`.
- **Gate friction fixes**: born from the 2026-08-15 cross-journal analysis (21/107 runs hit gate blocks; 330 blocked actions, 56% on `end_turn`; 40 no-op retries). ① Council-intent closure now accepts legacy `failed` decisions (pre-a0b487c out-of-band writes) as terminal accounting — they no longer demand a follow-up cancel; `retryable` still requires an explicit answer by design. ② Removed the `surprise_score >= 0.7 -> slow` hard rule: routing had become a function of global world noise, not of the action (T61 evidence: same args_hash routed slow at score 0.31 with an active major surprise, then fast once it cleared); surprise still weighs in via the additive term, and slow routes now carry `route_guidance` naming their exits. ③ `end_turn` gate rejections are self-describing: each pending authorization/council intent is listed with its decision id, state, and the exact next tool call, replacing the old category-name-only message that forced callers into `get_governance_brief` bookkeeping loops.
- **Notification arbitration review follow-ups**: two fixes from the adversarial pass. ① The `end_turn` turn report — the path where the dedication leftover actually surfaced (T97–T100 logs) — parsed notifications directly and bypassed the new arbitration; arbitration now lives in a single `GameState._arbitrate_notifications` choke point consumed by both `get_notifications` and `execute_end_turn`. ② The ruleset capability cache is cleared when `get_game_identity` detects a new game: the ruleset belongs to the game, not the process, and a stale cache would mis-arbitrate notifications and wrongly refuse dedication tools after loading a game with a different ruleset.
- **Notification capability arbitration**: fixes the dedication dead-lock from the hidden-jet-steppe-78 run (T56+: `NOTIFICATION_COMMEMORATION_AVAILABLE` rendered as "选择着力点" under Standard Rules for 40+ turns — unsatisfiable via `choose_dedication` (`NO_DEDICATIONS_IN_RULESET`), undismissable via `dismiss_popup` (it only handles UI popups, not the notification queue), never cleared). A notification is evidence of an engine event, not proof of a satisfiable obligation: `get_notifications` now downgrades expansion-only notices (dedications / world congress / governors) whose mechanic the active ruleset does not provide — annotated "引擎残留…忽略即可", moved out of Action Required, `resolution_hint` dropped. Both render paths (turn report + narrate) inherit the fix. `get_dedications`/`choose_dedication` short-circuit in Python once the ruleset is known (cache warmed by `get_game_overview`) with a self-explaining error instead of a bare Lua guard token; cold cache falls through to the Lua guard as before.
- **Gate friction review follow-ups**: three fixes from the adversarial review pass. ① Legacy `failed` decisions are frozen like sanctioned terminal states — the validator rejects reopening them, matching their role as final intent accounting. ② Re-routing an intent (the documented slow-route exits) now supersedes the stale `authorized`/`retryable` decision with an auditable `superseded_by_reroute` cancellation instead of stacking a second authorization that keeps blocking `end_turn`; in-flight (`executing`/`outcome_unknown`) decisions are never superseded, and budget locks stay reserved because replacement is not abandonment. ③ The `end_turn` gate message names a concrete recovery call for `current_turn_typed_snapshot_missing` and `governance_proposals_not_arbitrated` instead of a bare blocker code.
- **Council-intent deadlock closure (T97)**: `end_turn` was blocked for ~30 minutes at T97 of the france_2126806272 run (`council_action_intents_not_completed`). Two causes fixed (a0b487c): `update_belief_entity` previously accepted arbitrary `decision_state` values — an agent overwrote 9 decisions with an out-of-vocab `resolved` and erased 4 terminal `cancelled` states — so `decision_state` now has vocabulary validation and terminal states cannot reopen; and `record_action_verification` only settled `executing` — the `outcome_unknown` settlement branch was added, giving conservative `submitted` receipts an official exit (see [Governance System](docs/governance-system.md)). The affected journal was unlocked offline via the official cancel path (`offline-repair-t97-council-intents`).
- **World-model forecasting layer**: new pure `civ6_belief_engine.forecast` package — calibration report (Brier, reliability buckets, observability rate over resolved predictions), `TrendExtrapolator` behind a `Forecaster` protocol (conservative/baseline/aggressive branches persisted as tombstoneable `simulation` entities), and a deterministic Bayesian posterior for hypothesis pools. MCP tools `get_calibration_report` / `run_trend_forecast` / `rebalance_hypotheses_bayesian` in `server/tools/world_model.py`. Overdue-prediction dead end fixed: `review()` records `overdue_reason` and re-examines overdue predictions so late disconfirming metrics resolve them (`automatic_late`).
- **Belief coverage audit**: `civ6_belief_engine.coverage` + `scripts/belief_coverage.py` measure per-game and fleet `belief_supported_decision_ratio`, prediction resolution, and `beliefs_per_100_actions` from belief journals. Local replay shows the "belief-driven" claim is currently unsupported by data (0.8% of final decisions reference any belief) — decisions/actions/observations are pipeline-automatic, beliefs are opt-in tool calls.
- **Automatic belief/prediction derivation**: `civ6_belief_engine.derivation` rule registry turns query observations into entities automatically — barbarian camp threat beliefs, research/civic completion timing predictions, victory-race ETA predictions, and combat damage predictions with resolution from later evidence (`review()`'s evaluation rules act as safety net). All derived entities are tagged `derived`; the coverage audit splits them from agent self-reports.
- **Normalization**: `get_tech_civics` and `get_barbarian_overview` results are now normalized into facts/metrics; `get_game_overview` gains research/civic/era names and dark/golden thresholds. Fixed a pre-existing bug where `get_combat_estimate` matching failed for multi-word unit names ("Barbarian Warrior") — combat estimates were never normalized before.
- **Duplicate council-intent guard**: `BeliefEngine.find_duplicate_pending_intent` + a check in `submit_governance_proposal` refuse to re-authorize an action whose approved council intent is still unfinished. Born from the 2026-08-15 duplicate-settle incident (three parallel settle-capital authorizations deadlocked `end_turn` for ~30 minutes); regression tests replay the incident shape, including legacy decisions without `intent_id` clearing the turn gate after cancellation.
- **Run dashboard live wiring**: `civ6_belief_engine.dashboard` builds read-only dashboard state from a belief journal (masthead identity, empire metrics, chronicle with chapter breaks, ledger split, read-only turn-gate mirror); `scripts/run_dashboard.py` serves `design/run-dashboard.html` + `/state` on 127.0.0.1:8765. File-reading only — never touches FireTuner or the live MCP process, safe beside a live DSH session.

- **Product boundary**: renamed the Python distribution to `civ6-belief-engine`, moved Belief Engine/governance implementations to `src/civ6_belief_engine/`, and preserved the `civ_mcp` package, `civ-mcp` CLI, and `mcp__civ6__*` tool surface for compatibility.

- **HuggingFace dataset publisher**: End-to-end pipeline (`scripts/publish_hf/`) for staging, exporting parquet tables, generating Croissant 1.1 metadata, validating, and uploading to HuggingFace.
- **NeurIPS anonymization**: RAI metadata fields, identity redaction in parquet exports, anonymous HF account.
- **Game-over watchdog**: Detect victories even when the LLM stops calling tools — polls game state on a background timer.
- **Auto-resume on crash**: Increase max-retries to 20 for network resilience.
- **Admissibility badges**: Game IDs show admissibility status on game detail and benchmark pages.
- **8-dimension spider charts**: Model profile pages show radar charts with per-dimension scores, color-coded by domain.
- **Collaborator analysis toolkit**: `scripts/civbench_data.py` pandas library + SAS token generator for notebook-based analysis.
- **Advisor per-turn budget**: Soft warn at 10, hard cap at 20 calls across `get_district_advisor` + `get_wonder_advisor`. Fixes the 1,567-call loop that killed Gemini Pro at T136.
- **Save scumming detection**: Live deterrent during games + historical auditor for post-hoc analysis.
- **Single admissible boolean**: Precomputed on game doc — ELO query becomes one filter.
- **Game-over capture in end_turn**: `GameOverStatus` captured directly, eliminating fragile second Lua call.
- **Player elimination detection**: `IsAlive()` check when no winning team found.
- **Turn-limit detection**: `Game.GetMaxTurns()` check when no winner at game end.
- **Early diplomacy detection**: 45s threshold cuts 10-min dead wait per AI trade proposal.
- **10-min API timeout + 6 retries**: Prevents indefinite LLM hangs.
- **Live streaming**: In-progress games stream to Convex with quality gates.
- **Launch discipline**: Block dirty git tree + verify feature markers on remote machines.
- **Kimi-K2.5 support**: Register 256K context window with Inspect.
- **Sync safety**: Don't regress completed games to live when sync watcher delivers late rows.
- **Web**: Next.js 16.2.3, clickable model names, SEO (OG metadata, sitemap, llms.txt), UI polish (shimmer skeletons, victory glow, map playback 10x default).

### Repository hygiene

- **Removed legacy compatibility shims**: `civ_mcp/belief_engine.py` and `civ_mcp/governance/` were deleted; tests and code now import `civ6_belief_engine` directly. `test_product_package_boundary.py` now guards dependency direction instead of asserting shim forwarding.
- **Devlogs moved out of `docs/`**: `docs/devlog/` → top-level `devlog/` (12 game reports); docs now contain only operational/architecture documentation.
- **Archived dead scripts**: `generate_sas_token.py`, `scrape_wiki_images.py`, `split_game_log.py`, `menu_audit.py` moved to `scripts/_archive/` (zero references). `publish_hf_dataset.py` stays — it backs the HF dataset pipeline.
- **CI now runs the offline test suite**: the Python job previously only `py_compile`d `server.py`; it now installs pytest and runs `pytest tests/ -q --ignore=tests/test_scorer.py` (344 tests).

## v1.1.10 — Orchestrator Hardening (2026-04-15)

Final round of stability fixes before handing the fleet over to unattended overnight runs. The main theme is making the orchestrator less aggressive — it now reports problems rather than trying to auto-fix them, which caused more damage than it prevented.

- **Orchestrator discovery**: Safer machine discovery, `needs_attention` status, no auto-kills.
- **Package verification**: Clear heartbeat and verify installed packages before dispatch.
- **Linux CONTINUE position**: Verified positional click for Linux windowed mode.
- **Disable GameCore pre-check on Linux**: Main menu has no Lua states — the pre-check was always failing and wasting 30s.

## v1.1.9 — Game-Over Detection (2026-04-14)

Multiple models were reaching game-over states that the server didn't detect — the agent would keep playing into a post-victory state, or the defeat screen would appear but the Lua query missed it. This release adds fallback detection paths for every game-over variant.

- **Defeat screen detection**: GameCore fallback + polling when standard detection fails.
- **Linux OCR geometry**: Use actual capture geometry for windowed click offset.
- **AI turn timeout**: Extended to 10 minutes (was 5). Some late-game turns with 8 AI civs genuinely take this long.
- **Game process timeout**: Increased to 60s for slow launches on underpowered machines.
- **Inspect eval fix**: Upgrade + compaction + stderr capture to prevent silent death.
- **Windows CMD fix**: Reverted `start /min` which prevented game from launching — the hidden window approach doesn't work with Steam's process model.

## v1.1.8 — Orchestrator v2 (2026-04-13)

Complete rewrite of the orchestrator's core loop. The v1 orchestrator was a flat script that dispatched jobs sequentially; v2 introduces a proper state machine with per-machine state files so multiple orchestrator instances (one per operator) can coordinate without overwriting each other.

- **Orchestrator v2**: Job matrix, state machine, resume-on-crash.
- **Per-machine state files**: Concurrent orchestrators no longer overwrite each other.
- **Windows CMD**: Hidden window for background launches.

## v1.1.7 — Scoring + Timeout (2026-04-10)

The big addition is the 8-dimension scoring rubric. Each completed game is automatically scored across Overall, Economic, Military, Scientific, Diplomatic, Spatial, Tool Fluency, and Coherence. These scores power the spider charts on the web dashboard and the `scorecard` CLI command for model comparison.

The AI turn timeout was also bumped from 40s to 5 minutes. The v1.0.3 post-mortem showed that T249 hangs were probabilistic, not deterministic — the AI just needed more time on complex late-game turns with 8 civilizations.

- **8-dimension scoring**: `score` and `scorecard` commands in analyze.py.
- **AI turn timeout**: Extended from 40s to 5 minutes (probabilistic hangs need more time).
- **Heartbeat threshold**: Unified 600s for boot and playing phases.
- **Victory type detection**: VP_DATA cross-check for Score victory fallback.
- **Linux autosave fix**: `Network.SaveGame` silently fails on Aspyr port — use GameCore path instead.
- **Inspect eval**: Fix silent death via upgrade + compaction + stderr.
- **Dependencies**: Upgrade google-genai 1.68→1.70, aiplatform 1.139→1.145.
- **Gemini 3 Flash**: Added ETA prior and model alias.

## v1.1.6 — World Congress + Provenance (2026-04-08)

The `vote_world_congress` tool was setting votes but never submitting them, which caused agents to get stuck in an infinite end_turn loop whenever the World Congress convened. Replaced with `queue_wc_votes` which batches votes and submits in a single call.

This release also adds `mcp_git_describe` to every diary row, so each data point is traceable to the exact server version that produced it — critical for data quality auditing.

- **Removed `vote_world_congress`**: Replaced by `queue_wc_votes` (old tool set votes but never submitted, causing infinite end_turn loops).
- **Provenance tagging**: `mcp_git_describe` added to playerRows schema.
- **Linux process cleanup**: Kill stale civ-mcp processes in kill_runner.
- **CONTINUE button**: Widen click grid for small windowed resolutions.
- **Heartbeat**: Handle non-integer turn values gracefully; bump playing threshold to 600s.
- **Flash Lite ETA**: Separate prior (was incorrectly using Gemini Pro's 5 min/turn).
- **Leader portraits**: Fix raw enum names like `LEADER_HAMMURABI`.
- **Stale games**: Add `markCompleted` Convex mutation.

## v1.1.5 — LOS + Space Projects (2026-04-05)

Two agent-facing bugs that caused repeated failures in specific game phases. Ranged units were trying to attack targets they couldn't see (the target list didn't check line of sight), and space projects showed `[READY]` even when prerequisite projects hadn't been completed — causing agents to queue unbuildable items and stall.

- **Ranged attack LOS check**: Add line-of-sight filter to ranged attack target list. Prevents `ERR:NO_LOS` errors that confused agents into retrying the same attack.
- **Space project UI**: Fix misleading `[READY]` on space projects that can't be built yet (missing prereq projects).
- **`_emitter` fix**: Resolve `UnboundLocalError` when telemetry emitter wasn't initialized.
- **Code formatting**: Bulk ruff format across src/, scripts/, evals/.

## v1.1.4 — Preflight + Web Polish (2026-04-03)

The fleet was experiencing too many silent failures — games that never started because Steam wasn't running, or packages weren't synced, or the telemetry bucket was unreachable. This release adds a comprehensive preflight check that catches all of these before wasting a 15-hour game slot.

- **Preflight checks**: Verify Steam running, no stale processes, telemetry bucket accessible, API credentials valid, uv packages synced.
- **Autosave fix**: Only clean autosaves on first attempt, not retries (retries were deleting the save they needed to load).
- **Web app**: Surface run IDs, data provenance, and eval track. Normalize design tokens, harden accessibility.
- **Heartbeat fixes**: Fix stale false-positive, TypeError in turn comparison.
- **Linux OCR**: Revert frame extent compensation and combined click chain.

## v1.1.3 — Orchestrator Observability (2026-04-01)

The orchestrator couldn't reliably tell whether a remote game was alive. Process detection via SSH was flaky (Windows `Get-Process` misses Session 1 processes, Linux `pgrep` races). The heartbeat file solves this: the MCP server writes its phase, turn, and PID on every tool call, and the orchestrator reads it via SSH.

- **Heartbeat file** (`~/.civ6-mcp/heartbeat.json`): MCP server writes phase, turn, PID on every tool call. Orchestrator reads via SSH instead of unreliable process detection.
- **Windows process killing**: PowerShell `Stop-Process` via EncodedCommand replaces `taskkill` (which can't reach Session 1 from SSH).
- **Autosave cleanup**: Clears both MCP autosaves and game `AutoSave_*` files before launch, preventing wrong-save loads. Fixed Linux path escaping for `Sid Meier's` apostrophe.
- **Phase-aware staleness**: Boot phases get 5 min threshold, playing gets 3 min.
- **Boot timeout**: 10 min hard limit for games that never reach gameplay.
- **Linux OCR**: Reverted frame extent compensation (xdotool already returns content area). Reverted combined click chain that broke GNOME input.

## v1.1.2 — Heartbeat System (2026-04-01)

- **Heartbeat file system**: Atomic JSON writes at `~/.civ6-mcp/heartbeat.json` with phase/turn/ts/pid.
- **Orchestrator reads heartbeat** instead of `Get-Process` on Windows (false negatives on schtasks sessions).
- **Status line** shows boot phase (`launching`, `connecting`, `loading`, `playing`).
- **Periodic status logging** to file every 5 minutes.

## v1.1.1 — Fleet Launch Fixes (2026-04-01)

The first batch of fleet runs exposed a long tail of platform-specific launch failures. Linux couldn't launch via Steam URI, killing the game also killed Steam, zombie processes blocked relaunch, and Windows PowerShell quoting broke the runner detection. Most of these were only visible when running headless via SSH — the local development path worked fine.

- **Linux game launch**: `steam -applaunch 289070` replaces unreliable URI scheme.
- **Kill game safety**: `pkill -x` exact match prevents killing Steam.
- **Stale process detection**: Kill zombie game processes that block relaunch.
- **Gemini 3.1 Flash Lite** added to model catalogue.
- **Menu audit script** for per-machine OCR calibration.
- **Crash recovery**: Connection-loss auto-restart after 5 consecutive failures; HANG recovery identity check retries.
- **Non-linear ETA**: Piecewise turn-time model with Bayesian blending.
- **Sleep detection**: Resets stall timers on host wake.
- **Sentinel safety**: Completion file only created on success.

## v1.1.0 — Orchestrator + Fleet Tooling (2026-03-31)

The transition from single-machine to multi-machine evaluation. The orchestrator dispatches (model, scenario) jobs to a fleet of machines via SSH, monitors health through heartbeat files, detects stalls, and auto-retries failed runs. Human-readable run IDs (`crimson-amber-falcon-47`) replaced hex hashes for easier debugging.

- **Orchestrator** (`scripts/orchestrator.py`): SSH-based fleet management with job dispatch, health monitoring, stall detection, auto-retry, and Convex sync.
- **Human-readable run IDs**: `adjective-color-noun-number` format (~34M unique IDs), deterministic from model+scenario+hour.
- **Data quality gates**: `should_sync_game()` skips <10 turns, `excludeReason` field for invalid games, Elo excludes development track.
- **CORRUPTED_QUEUE fix**: Auto-clear ghost queue entries, changed narration from "load autosave" to "set new production".
- **Wrong save loading**: Screen-aware detection, autosave cleanup, multi-position click grid, Lua reload fallback.

## v1.0.4 — Production Diagnostics (2026-03-30)

Small fixes that surfaced during the first real eval runs. Agents were confused by Great People showing 1 charge remaining after being consumed, by `set_research` silently accepting already-completed techs, and by opaque production failures.

- Great Person charges fix (consumed GP showed 1 remaining).
- Already-completed tech/civic detection in `set_research`.
- `CANNOT_PRODUCE` reasons in `set_city_production`.

## v1.0.3 — Crash Recovery (2026-03-30)

Post-mortem from the first GPT-5.4 eval run. A probabilistic hang at T249 wasted 2.1 hours because the single-retry recovery declared it "deterministic" after one attempt. The agent then loaded progressively older saves in a Groundhog Day loop. After relaunch, Civ 6 auto-loaded a Korea game from a different session because there was no post-load verification.

- Multi-retry hang recovery (3 attempts with escalating waits: 0s, 15s, 30s).
- Post-load civ identity verification (catches wrong-game-loaded after restart).
- Autosave retention increase (keep=5 → keep=8) for more fallback options.
- Fix SAVE_DIR → SINGLE_SAVE_DIR in save-existence check.

## v1.0.2 — Combat + Analysis (2026-03-30)

- **Combat followup fix**: Uses estimate as fallback (Lua state stale within same turn frame).
- **Promotion XP threshold fix**: `GetExperienceForNextLevel()` replaces double-counting formula.
- **City attack errors**: Split into `ERR:OUT_OF_RANGE`, `ERR:ALREADY_FIRED`, `ERR:NO_LOS`.
- **Builder tasks**: Filter by prerequisite tech (`_LOCKED` tag).
- **Analysis tools**: Sensorium metrics, reflection-action gap analysis.

## v1.0.1 — Combat Visibility (2026-03-29)

- Combat visibility improvements, movement diagnostics, trade reporting.

## v1.0.0 — Initial Release (2026-03-29)

The first tagged release. 76 MCP tools covering the full Civilization VI gameplay loop — units, cities, research, diplomacy, trade, government, religion, great people, world congress, and victory tracking. Eval infrastructure built on Inspect AI with three standardised scenarios (Ground Control, Snowflake, Cry Havoc). Real-time game dashboard on Convex with Elo rankings and strategic map replay. Telemetry pipeline to Azure Blob Storage. Cross-platform support for macOS, Windows, and Linux.

- MCP server with 76 tools for Civilization VI via FireTuner.
- CivBench eval infrastructure with Inspect AI integration.
- Convex dashboard with real-time game tracking, Elo rankings, strategic map replay.
- Azure Blob telemetry storage.
- Support for macOS, Windows, and Linux.
