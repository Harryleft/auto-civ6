# Documentation

## For Users

- [Getting Started](../README.md#quick-start) — Setup and first game
- [Tool Reference](https://civbench.vercel.app/docs/tools) — MCP tool reference (web)

## Agent Operations

- [AGENTS.md](../AGENTS.md) — Short rules, hard boundaries, and document routing
- [Civ 6 + DSH Startup](agent-startup.md) — FireTuner, port 4318, DSH launch, and live acceptance gates
- [Turn Loop](agent-turn-loop.md) — Query order, governance gates, diary fields, and periodic checks
- [精简游玩配置](lean-play-profile.md) — `--play-profile legacy|lean`：治理关闭、控制面工具边界、反思字段与回退方式
- [回合局面简报](turn-context.md) — 独立于治理的每回合输入：字段白名单、缺失语义、首次变更门槛与 end_turn 交接
- [Strategy](agent-strategy.md) — Deity survival, expansion, diplomacy, war, and victory paths
- [Tool and Action Reference](agent-tools.md) — Offline helpers, actions, blockers, production, and World Congress
- [Game Recovery](agent-recovery.md) — Autosaves, hung AI turns, and recovery commands
- [Game Mechanics Knowledge Base (Wiki)](wiki/README.md) — Official-wiki distilled mechanics per domain, progressive disclosure (L0 decision cheat-sheet → L1 mechanics → L2 data)

## For Developers

- [Product architecture](product-architecture.md) — `civ6-belief-engine` product boundary and unchanged MCP compatibility surface
- [Architecture](architecture-diagrams.md) — Full stack from tool call to game engine, wire protocol, Lua contexts
- [Business Dataflow](business-dataflow.md) — End-to-end business logic and data flow diagrams (Mermaid)
- [Belief Engine](belief-engine.md) — Event-sourced world model: events, run modes, derivation rules, coverage audit
- [Governance System](governance-system.md) — Control loop, engineering constraints, proposals/council/budget locks, end-turn gate
- [Testing](testing.md) — Test taxonomy, shared fixtures, and unified pytest configuration
- [实验基线清单](experiment-baseline.md) — 对照运行前的可复现基线记录（提交／模型／规则／存档哈希）与 end_turn 超时预算
- [状态分类与缓存](cache-policy.md) — 规则复用、采集失效、图变化与 5／10／20 回合历史窗口
- [Observability](observability.md) — Diary, tool logging, and spatial attention tracking
- [Save File Format](save-file-format.md) — Reverse-engineered .Civ6Save structure
- [Bypassing the Aspyr Launcher](research/bypassing_aspyr_launcher.md) — macOS launch automation

## Evaluation & Benchmarks

- [Benchmark Scenarios](paper/scenario-spec.md) — Three eval scenarios (Ground Control, Snowflake, Cry Havoc)
- [Game Reports](../devlog/) — 12 full game logs with strategic post-mortems
- [Cross-Game Analysis](cross-game-analysis.md) — Recurring failure patterns across games 1–4

## Design & Research

- [Agent vs Agent](agent-vs-agent.md) — Multi-agent play design (proposal, not implemented)
- [Feature Ideas](feature-ideas.md) — Planned features with status markers
- [Unknown-Unknowns Audit](audit-unknown-unknowns.md) — Belief-engine blind-spot audit with fixes (2026-08)
- [MCP Design Report](research/game_mcp_design_report.md) — Initial design research and best practices

## Essays

- [The Hallucination of Competence](agent-essays/the-hallucination-of-competence.md) — Gemini's self-analysis of strategic narrative bias (Game 12)

## Archive

- [Idea Research](research/idea_research.md) — Pre-implementation research (2025), superseded by [Architecture](architecture-diagrams.md)
