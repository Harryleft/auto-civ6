# Civ Belief Engine

The Belief Engine is the persistent world model between game observation and
action.  It records what the agent observed separately from what the agent
believes, predicts, plans, and eventually verifies.

The implementation lives in `src/civ6_belief_engine/`; `civ_mcp` exposes the
MCP adapter. The legacy `civ_mcp.belief_engine` / `civ_mcp.governance`
compatibility shims were removed; import the product package directly.

## Runtime modes

Set `CIV_MCP_BELIEF_MODE` before starting the MCP server:

- `enforce` (default): preserves the existing governance snapshot, belief
  context, action preflight, and event recording behavior.
- `observe`: records observations, but removes governance snapshots, appended
  belief context, and belief-based action gates from the gameplay hot path.
- `off`: bypasses belief event recording as well as all `observe` bypasses.

Use `off` for a minimal A/B run against the legacy path. Core game validation,
end-turn safety, and autosave behavior remain independent of this setting.
`get_game_overview` reports the resolved mode and capabilities as `RUNTIME
POLICY`; Agent hosts should consume that contract instead of maintaining their
own mode tables.

## Runtime loop

```text
MCP query -> automatic Observation -> normalized metrics
          -> get_turn_brief / get_game_overview decision context
          -> Belief / Hypothesis revision
          -> Prediction and dynamic-plan review
          -> Fast / verify / slow routing
          -> game action -> automatic verification trace
          -> Surprise / Contradiction -> replan
```

Every entity supports current-state CRUD.  Deletes are tombstones: the entity
disappears from active state, but the append-only event remains available to
`get_belief_trace` for prediction scoring and post-game attribution.

Local state is stored per game at:

```text
~/.civ6-mcp/beliefs/belief_<civilization>_<seed>.jsonl
```

Telemetry mirrors the same events into each run's `beliefs.jsonl`, allowing
the Convex sync pipeline and game-detail dashboard to materialize current
entities without discarding the audit history.

Raw MCP results belong to the transcript/telemetry layer, not to strategic
state. New Belief Engine events keep only normalized facts/metrics, a
SHA-256 `result_ref`, and links between decisions, actions, outcomes, and
observations. They do not copy the raw result into each entity. Existing event
logs remain readable and are not rewritten in place.

## Single-track tool results

All read-only query tools return a single-track JSON envelope built by
`civ_mcp.facts`:

- 世界状态：`get_units`, `get_cities`, `get_map_area`, `get_barbarian_overview`,
  `get_village_overview`, `get_strategic_map`, `get_empire_resources`
- 军事与移动：`get_combat_estimate`, `get_pathing_estimate`,
  `get_unit_promotions`, `get_spies`
- 科研与胜利：`get_tech_civics`, `get_victory_progress`, `get_era_progress`,
  `get_dedications`
- 经济与扩张：`get_city_production`, `get_settle_advisor`,
  `get_global_settle_advisor`, `get_trade_routes`, `get_trade_destinations`,
  `get_builder_tasks`, `get_trade_options`, `get_purchasable_tiles`
- 治理与外交：`get_policies`, `get_notifications`, `get_pending_trades`,
  `get_pending_diplomacy`, `get_diplomacy`, `get_governors`, `get_city_states`
- 宗教与气候：`get_pantheon_beliefs`, `get_religion_beliefs`,
  `get_religion_spread`, `get_religion_overview`, `get_climate_overview`,
  `get_world_congress`
- 伟人与顾问：`get_great_people`, `get_great_people_overview`,
  `get_gp_advisor`, `get_district_advisor`, `get_wonder_advisor`
- 记忆：`get_diary`（本地事件日志，非游戏事实）

Exceptions（保持原契约）：`get_game_overview`（回合强制入口，自身即 JSON
摘要 + RUNTIME POLICY）、`propose_trade`（动作工具，其 test 模式是只读
变体但结果走动作回执检测）、信念/治理控制面工具与 `run_lua`。

Example (`get_units`):

```json
{
  "v": 1,
  "tool": "get_units",
  "turn": 56,
  "source": "civ_mcp:GameState",
  "coverage": {"own_units": "COMPLETE", "foreign_units": "CURRENTLY_VISIBLE"},
  "facts": {"own_units": [{"unit_id": 131073, "x": 32, "y": 37, ...}]}
}
```

The model and the observation normalizer both consume `facts` (field-level
schema, no free-text parsing). `coverage` uses the graph-plan three-value
semantics — `COMPLETE` (absence is real), `CURRENTLY_VISIBLE` (absence only
means not currently seen), `KNOWN_HISTORY` (revealed history; unobserved is
not deleted). Belief-engine context is merged into the envelope as a
`belief_context` key instead of a trailing text block, so the JSON stays
parseable. 历史日志中的旧信封（仍带 `narrated` 键）解析宽松，只读 `facts`。

`normalize_tool_result` 对信封直接从 `facts` 提取 facts/metrics（reliability
1.0）；纯文本结果（目前只有 `get_game_overview` 实时出现）走兼容正则回退。

## Journal durability

The journal is the system of record, so three properties are enforced rather than
assumed:

- **Single writer.** `bind_game` takes an exclusive advisory lock on
  `<journal>.lock` (`LOCK_EX|LOCK_NB`; `fcntl` on POSIX, `msvcrt` on Windows) and
  raises `BeliefEngineError` if another process holds it. Without it a second
  writer would interleave sequence numbers, and — because `_load` rewrites the
  file when it meets a torn line — could lose events outright. The lock is held
  per process, so reloading a journal inside one process stays legal.
- **Bounded replay cost.** `replay_graph_events(events, verify=...)` defaults to
  `"final"`, checking only the newest recorded `state_hash`. `GraphView.state_hash`
  serializes the whole node/edge set, so per-delta verification is O(D×V): on a
  real 110-turn journal (420 deltas, 4,269 nodes / 5,854 edges) it cost 46.7s of a
  48.4s load. Use `"all"` to locate the exact diverging delta; `"none"` skips hash
  comparison while still raising structural `GraphInvariantError`s.
- **Reload marking.** Loading a save rewinds the world while the append-only log
  still describes the abandoned future. `pipeline._record_game_reload_epoch` is
  the single entry point and **every** save-loading path must call it — the
  connection-recovery branch, the `restart_and_load` tool, and end-turn hang
  recovery. It bumps the epoch, voids the abandoned branch's authorizations, and
  never raises: a bookkeeping failure must not break the recovery in progress.

## Entity model

- `observation`: directly observed fact, source, reliability, result
  fingerprint, and normalized metrics. Successful `get_*` MCP calls are
  captured automatically; raw text remains in the transcript/telemetry owner.
- `belief`: an interpretation with probability, confidence, impact, urgency,
  supporting evidence, counter-evidence, falsifiers, and expectations.
- `hypothesis`: one competing explanation in a topic pool. Use
  `rebalance_hypothesis_pool` to update the whole probability distribution.
- `prediction`: falsifiable statement, probability, deadline, and optional
  metric rule. False high-confidence predictions create Surprise entities.
- `plan`: 5/10/20-turn goal with assumptions, success criteria, exit criteria,
  and a scheduled review turn.
- `simulation`: one projected future branch (scenario, assumptions, metric
  projections) written by `run_trend_forecast`; superseded runs are
  tombstoned and the audit history remains replayable.
- `contradiction`: generated when an observed metric violates a belief's
  declared expectation.
- `world_entity`: typed `GameState` node with stable identity and graph links.
- `goal`, `proposal`, `critic_review`, `council_decision`, and `budget_lock`:
  national-governance state using the same event log and tombstone semantics.
- `decision`: Fast/Slow routing result plus a hash-bound structured action intent.
- `action`: automatic MCP action result linked to its consumed decision, or an
  explicit decision verification.
- `attribution`: candidate failure causes with evidence-weighted posteriors.

Probability describes the event; confidence describes the quality of the
estimate. The first implementation measures revision direction and latency—it
does not claim that model-generated percentages are statistically calibrated.

## Ruleset compatibility

The engine observes the active Civ VI ruleset rather than treating installed
database rows as enabled mechanics. Standard Rules omits Governors,
Ages/Dedications, Alliances, Diplomatic Favor, strategic-resource stockpiles,
and World Congress. Their MCP queries return explicit ruleset errors and do
not create successful observations. Basic diplomacy, owned/nearby resources,
combat estimates, and all other shared systems remain available.

## Declarative conditions

Predictions, belief expectations, and plan exit conditions use the same JSON
rule:

```json
{"metric":"science","operator":">=","value":60}
```

Supported operators are `>=`, `>`, `<=`, `<`, `==`, `!=`, `contains`, and
`not_contains`. `get_game_overview` currently normalizes `turn`, `score`,
`gold`, `gold_per_turn`, `science`, `culture`, `faith`, `favor`, `cities`,
`population`, `units`, `exploration_pct`, and `era_score`. Diplomacy metrics
use keys such as `diplomacy.player_3.at_war` and
`diplomacy.player_3.military`.

## MCP workflow

1. Start each turn once with `get_game_overview`. It returns the authoritative
   `RUNTIME POLICY`; in `enforce` mode the same call also captures and returns
   the typed governance snapshot. Do not immediately duplicate it with
   `get_governance_brief`.
2. Follow the policy capabilities. Use `get_governance_brief` only to refresh
   or recover an enforce-mode snapshot, and use `get_turn_brief` only after
   material evidence, an action outcome, or context recovery.
3. Call `get_belief_state` only when the full current world model is needed;
   the overview and targeted queries are the normal decision inputs.
4. Record important interpretations with `upsert_belief` and competing
   explanations with `upsert_hypothesis`.
5. Add falsifiable claims with `upsert_prediction` and explicit 5/10/20-turn
   commitments with `upsert_dynamic_plan`.
6. For governed strategic choices, use `upsert_strategic_goal`,
   `submit_governance_proposal`, an optional `review_governance_proposal`, and
   `resolve_governance_council`. Critics may agree; an objection is rejected
   unless it cites a stored Observation or identifies an invalid assumption and
   concrete alternative. The council uses hard constraints, budget locks,
   priority, Pareto dominance, and opportunity cost without reducing national
   strategy to one weighted score.
7. When the runtime policy reports enforced routing, call
   `route_belief_decision` for governed actions and pass the council-selected
   structured `action_intent`. The common harness wrapper consumes this
   authorization exactly once; calling a gated action without it is rejected
   before touching the game, and a `slow` route remains blocked.
   A `verify_then_fast` route requires the declared fresh query with matching
   parameters and required fact/metric keys. An unrelated `get_*` result cannot
   satisfy the gate.
8. Treat nearby hostile units as a verification trigger, not as evidence that
   a route is unsafe. Call `get_combat_estimate`, then pass its effective
   strengths, HP, modifiers, and expected damage to `assess_route_combat_risk`.
   Without that complete quantitative assessment, the route belief is not
   changed.
9. In a recording mode, call `get_turn_brief` after material new evidence or
   an action outcome.
   The harness already stores every successful action result as an
   Observation and runs review automatically; this call exposes the next
   decision gate. Overview and normal `get_*` queries include a compact gate
   context in their output.
10. Link the selected decision to its real outcome with
   `record_action_verification` when the normal MCP result is insufficient.
11. Read `get_belief_metrics` and `get_belief_trace` for calibration and
   post-game analysis. `get_calibration_report` adds Brier score,
   reliability buckets per claimed-probability band, and the observability
   rate — how many predictions were checkable at deadline at all.

Overdue predictions are not a dead end: `review()` records an
`overdue_reason` (`metric_unavailable` vs `no_evaluation_rule`) and keeps
re-examining them, so evidence arriving after the deadline that disproves
the rule still resolves the prediction (`automatic_late`); a late
confirmation cannot prove the claim held *by* the deadline and stays open.

Forecasting is a pure layer over the same event log
(`civ6_belief_engine.forecast`): `run_trend_forecast` projects metric
futures under conservative/baseline/aggressive trend scenarios into
`simulation` branches the council can cite, and
`rebalance_hypotheses_bayesian` shifts a hypothesis pool from supplied
likelihood ratios — the posterior arithmetic runs server-side and lands in
the event log. Higher-fidelity forecasters (e.g. save-state replay) plug
into the same `Forecaster` protocol.

Use `update_belief_entity` for corrections and `delete_belief_entity` for
current-state deletion. `decision_state` is exempt: writing it directly (the
2026-08-15 turn-97 incident wrote `resolved` over live and cancelled decisions
alike) both leaves the turn gate blocked and removes the official closure
paths, so the engine rejects such writes and only the lifecycle tools —
`record_action_verification`, `cancel_routed_action`, and the routed-action
pipeline itself — may move it. Do not encode interpretation into Observation
text; that destroys the fact/inference boundary the engine is intended to
measure.

## Coverage audit

The pipeline auto-records Observations, Actions, and a routed Decision for
every gated tool call, but Beliefs and Predictions only exist when they are
explicitly written. That asymmetry is exactly what the coverage metrics
measure — per game, not as an aggregate claim:

```bash
./scripts/belief_coverage.py            # table for ~/.civ6-mcp/beliefs/
./scripts/belief_coverage.py --json     # machine-readable summaries + fleet totals
```

Key numbers: `belief_supported_decision_ratio` (decisions whose
`belief_ids` resolve to recorded belief rows; tombstoned beliefs count,
dangling ids do not), `predictions_resolved`, `beliefs_per_100_actions`.
Journals are replayed read-only with last-write-wins on entity snapshots —
the reducer never touches a live game. The same functions live in
`civ6_belief_engine.coverage` for dataset/fleet audits.

## Automatic derivation

Beliefs and predictions no longer depend on the agent remembering to call
`upsert_belief`/`upsert_prediction`. Every successful query observation is
offered to a rule registry (`civ6_belief_engine.derivation`), and matching
rules create, update, retire, and resolve entities with evidence references:

| Rule | Tool | Produces | Resolves when |
|---|---|---|---|
| Barbarian camp threats | `get_barbarian_overview` | belief per camp (probability by distance) | camp unseen for 5 consecutive overviews (archived); re-seen camps resurrect the same entity |
| Rival military threat | `get_diplomacy` | belief per rival at war or ≥2× our military (probability by war+ratio); a slow-variable trend refreshed only by the periodic diplomacy check, not a real-time alert | peace + ratio < 1.5×, or rival absent 10 turns → archived; re-escalation resurrects |
| Great people race pressure | `get_great_people_overview` | belief per class where a rival leads (probability by gap ratio) | we take the lead, or gap > 50% of leader points → archived |
| Research/civic timing | `get_tech_civics` | prediction "X completes by T" | subject no longer researched: completed-counter moved → confirmed; deadline passed → disconfirmed; plus an `evaluation` metric safety net handled by `review()` |
| Victory race ETA | `get_victory_progress` | rate-based ETA prediction per rival section (≥15% progress) | `review()` resolves on VP arrival via the `evaluation` rule |
| Combat damage | `get_combat_estimate` | prediction "~D damage" with defender HP baseline | next estimate shows the HP drop; tolerance `max(3, 25%)` |

Design invariants: facts stay facts (deterministic values become
predictions, only genuine inference becomes belief); entity ids are stable
per subject so repeated queries never duplicate journal events; every
derived entity is tagged `derived` so the coverage audit separates
system-generated beliefs from agent self-reports (`b_auto`/`p_auto`
columns in `scripts/belief_coverage.py`); rule failures are logged and
never break recording. A running game only picks the registry up after the
MCP process restarts.
